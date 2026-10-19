use std::net::TcpListener;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// The OAuth redirect URI registered with CloudHealth is http://127.0.0.1:8080/oauth-callback,
// so the backend must listen on exactly this port.
const BACKEND_PORT: u16 = 8080;
const BACKEND_URL: &str = "http://127.0.0.1:8080";

/// Keeps the sidecar handle so we can stop it when the app quits.
struct Backend(Mutex<Option<CommandChild>>);

/// Shows a status line on the splash page (the page is `dist/index.html`).
fn splash_status(app: &tauri::AppHandle, msg: &str) {
  if let Some(win) = app.get_webview_window("main") {
    let js = format!(
      "var e=document.getElementById('status-text'); if(e) e.innerText={};",
      serde_json::to_string(msg).unwrap_or_else(|_| "\"\"".into())
    );
    let _ = win.eval(&js);
  }
}

/// Appends backend diagnostic messages directly to ~/.cleo/cleo.log so no crash or stderr is lost.
fn append_to_cleo_log(prefix: &str, text: &str) {
  let home = std::env::var_os("USERPROFILE")
    .or_else(|| std::env::var_os("HOME"))
    .map(std::path::PathBuf::from);
  if let Some(home) = home {
    let cleo_dir = home.join(".cleo");
    let _ = std::fs::create_dir_all(&cleo_dir);
    let log_file = cleo_dir.join("cleo.log");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(log_file) {
      use std::io::Write;
      let _ = writeln!(f, "[Tauri Backend {}] {}", prefix, text.trim_end());
    }
  }
}

/// True if something that looks like Cleo already answers on the backend port.
async fn cleo_already_running(client: &reqwest::Client) -> bool {
  match client.get(format!("{BACKEND_URL}/health")).send().await {
    Ok(resp) if resp.status().is_success() => resp
      .text()
      .await
      .map(|t| t.contains("\"ai_engine_key\""))
      .unwrap_or(false),
    _ => false,
  }
}

fn request_http_shutdown() {
  if let Ok(mut stream) = std::net::TcpStream::connect_timeout(
    &std::net::SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT)),
    Duration::from_millis(400),
  ) {
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let req = format!(
      "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n",
      BACKEND_PORT
    );
    use std::io::Write;
    let _ = stream.write_all(req.as_bytes());
    let _ = stream.flush();
    std::thread::sleep(Duration::from_millis(250));
  }
}

/// Asks the sidecar to exit cleanly (HTTP shutdown + taskkill /T on Windows, SIGTERM on Unix,
/// which unloads local models and releases all memory), then terminates any remaining process handles.
fn stop_backend(child: CommandChild) {
  request_http_shutdown();

  #[cfg(target_os = "windows")]
  {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = std::process::Command::new("taskkill")
      .args(["/F", "/T", "/PID", &child.pid().to_string()])
      .creation_flags(CREATE_NO_WINDOW)
      .status();
    let _ = std::process::Command::new("taskkill")
      .args(["/F", "/IM", "cleo-server.exe"])
      .creation_flags(CREATE_NO_WINDOW)
      .status();
    let _ = std::process::Command::new("taskkill")
      .args(["/F", "/IM", "cleo-server-x86_64-pc-windows-msvc.exe"])
      .creation_flags(CREATE_NO_WINDOW)
      .status();
  }

  #[cfg(unix)]
  {
    let _ = std::process::Command::new("kill")
      .arg("-TERM")
      .arg(child.pid().to_string())
      .status();
    std::thread::sleep(Duration::from_millis(800));
  }

  let _ = child.kill();
}

#[cfg(target_os = "windows")]
fn ensure_windows_sidecar() -> Option<std::path::PathBuf> {
  const SIDECAR_BYTES: &[u8] = include_bytes!("../binaries/cleo-server-x86_64-pc-windows-msvc.exe");

  // Priority 1: Next to the running executable (e.g. C:\Users\user\Downloads\cleo-server.exe)
  if let Ok(current_exe) = std::env::current_exe() {
    if let Some(exe_dir) = current_exe.parent() {
      let exe_sidecar = exe_dir.join("cleo-server.exe");
      let triple_sidecar = exe_dir.join("cleo-server-x86_64-pc-windows-msvc.exe");

      for candidate in [&exe_sidecar, &triple_sidecar] {
        if candidate.exists() {
          if let Ok(meta) = candidate.metadata() {
            if meta.len() == SIDECAR_BYTES.len() as u64 {
              log::info!("[Sidecar] Reusing verified sidecar: {}", candidate.display());
              return Some(candidate.to_path_buf());
            }
          }
        }
      }

      if std::fs::write(&exe_sidecar, SIDECAR_BYTES).is_ok() {
        log::info!("[Sidecar] Extracted sidecar to: {}", exe_sidecar.display());
        return Some(exe_sidecar);
      }
    }
  }

  // Priority 2: User profile ~/.cleo/bin/cleo-server.exe (guaranteed user write permissions)
  if let Some(user_home) = std::env::var_os("USERPROFILE") {
    let cleo_bin = std::path::PathBuf::from(user_home).join(".cleo").join("bin");
    let user_sidecar = cleo_bin.join("cleo-server.exe");
    if user_sidecar.exists() {
      if let Ok(meta) = user_sidecar.metadata() {
        if meta.len() == SIDECAR_BYTES.len() as u64 {
          log::info!("[Sidecar] Reusing user-dir sidecar: {}", user_sidecar.display());
          return Some(user_sidecar);
        }
      }
    }
    if std::fs::create_dir_all(&cleo_bin).is_ok() {
      if std::fs::write(&user_sidecar, SIDECAR_BYTES).is_ok() {
        log::info!("[Sidecar] Extracted sidecar to: {}", user_sidecar.display());
        return Some(user_sidecar);
      }
    }
  }

  // Priority 3: %TEMP%/cleo/cleo-server.exe
  let temp_sidecar = std::env::temp_dir().join("cleo").join("cleo-server.exe");
  if temp_sidecar.exists() {
    if let Ok(meta) = temp_sidecar.metadata() {
      if meta.len() == SIDECAR_BYTES.len() as u64 {
        log::info!("[Sidecar] Reusing temp-dir sidecar: {}", temp_sidecar.display());
        return Some(temp_sidecar);
      }
    }
  }
  if let Some(temp_dir) = temp_sidecar.parent() {
    let _ = std::fs::create_dir_all(temp_dir);
  }
  if std::fs::write(&temp_sidecar, SIDECAR_BYTES).is_ok() {
    log::info!("[Sidecar] Extracted sidecar to: {}", temp_sidecar.display());
    return Some(temp_sidecar);
  }

  log::error!("[Sidecar] Failed to extract sidecar to any writable location");
  None
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  #[cfg(target_os = "linux")]
  std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");

  tauri::Builder::default()
    .plugin(tauri_plugin_opener::init())
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_http::init())
    .manage(Backend(Mutex::new(None)))
    .setup(|app| {
      if let Some(win) = app.get_webview_window("main") {
        let icon = tauri::include_image!("icons/icon.png");
        let _ = win.set_icon(icon);
      }

      let handle = app.handle().clone();

      tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::builder()
          .timeout(Duration::from_secs(2))
          .no_proxy() // never route loopback health checks through a corporate proxy
          .build()
          .expect("http client");

        // Port busy? Reuse a running Cleo; otherwise tell the user what's in the way.
        let port_free = TcpListener::bind(("127.0.0.1", BACKEND_PORT)).is_ok();
        if !port_free {
          if cleo_already_running(&client).await {
            log::info!("Cleo already running on port {BACKEND_PORT}; reusing it");
          } else {
            splash_status(
              &handle,
              &format!("Port {BACKEND_PORT} is in use by another program. Quit it and relaunch Cleo."),
            );
            return;
          }
        } else {
          // Launch the Python FastAPI backend sidecar (no browser tab, no self-update inside the bundle)
          #[cfg(target_os = "windows")]
          let sidecar_cmd = match ensure_windows_sidecar() {
            Some(path) => {
              log::info!("[Sidecar] Spawning Windows backend from: {}", path.display());
              handle.shell().command(path)
            }
            None => {
              log::warn!("[Sidecar] Fallback to sidecar('cleo-server')");
              handle.shell().sidecar("cleo-server")
                .unwrap_or_else(|_| handle.shell().command("cleo-server.exe"))
            }
          };

          #[cfg(not(target_os = "windows"))]
          let sidecar_cmd = match handle.shell().sidecar("cleo-server") {
            Ok(cmd) => cmd,
            Err(e) => {
              log::error!("Failed to resolve cleo-server sidecar: {}", e);
              splash_status(&handle, &format!("Could not start the Cleo backend: {e}"));
              return;
            }
          };

          let spawned = sidecar_cmd
            .env("PORT", BACKEND_PORT.to_string())
            .env("CLEO_NO_BROWSER", "1")
            .env("CLEO_OTA_SYNC", "1")
            .env("CLEO_TAURI_PID", std::process::id().to_string())
            .spawn();

          match spawned {
            Ok((mut rx, child)) => {
              log::info!("Cleo Python sidecar spawned (PID: {:?})", child.pid());
              if let Some(state) = handle.try_state::<Backend>() {
                *state.0.lock().unwrap() = Some(child);
              }

              let h = handle.clone();
              tauri::async_runtime::spawn(async move {
                let mut last_err = String::new();
                while let Some(event) = rx.recv().await {
                  match event {
                    CommandEvent::Stdout(line) => {
                      print!("[Backend] {}", String::from_utf8_lossy(&line));
                    }
                    CommandEvent::Stderr(line) => {
                      let text = String::from_utf8_lossy(&line).to_string();
                      eprint!("[Backend ERR] {}", text);
                      append_to_cleo_log("ERR", &text);
                      last_err.push_str(&text);
                      if last_err.len() > 2000 {
                        last_err = last_err.split_off(last_err.len() - 2000);
                      }
                    }
                    CommandEvent::Terminated(payload) => {
                      let term_line = format!("Process terminated: {:?}", payload);
                      log::warn!("[Backend] {}", term_line);
                      append_to_cleo_log("TERM", &term_line);

                      let error_line = last_err.lines().rev().find(|l| {
                        let t = l.trim();
                        !t.is_empty() && !t.starts_with("at ")
                      });

                      let status_msg = if let Some(err) = error_line {
                        format!("Cleo backend error: {}", err.trim())
                      } else {
                        format!(
                          "The Cleo backend stopped unexpectedly (exit code {:?}). See ~/.cleo/cleo.log",
                          payload.code
                        )
                      };
                      splash_status(&h, &status_msg);
                      break;
                    }
                    _ => {}
                  }
                }
              });
            }
            Err(e) => {
              log::error!("Failed to start cleo-server sidecar: {}", e);
              splash_status(&handle, &format!("Could not start the Cleo backend: {e}"));
              return;
            }
          }
        }

        // Wait for the backend, then load the dashboard. Done here (not in the splash page's
        // JavaScript) so the page never needs cross-origin access to the API.
        for attempt in 0..480u32 {
          if cleo_already_running(&client).await {
            if let Some(win) = handle.get_webview_window("main") {
              if let Ok(url) = BACKEND_URL.parse() {
                let _ = win.navigate(url);
              }
            }
            return;
          }
          if attempt == 40 {
            splash_status(&handle, "Still initializing… first launch can take a little while.");
          }
          tokio_sleep(250).await;
        }
        splash_status(&handle, "The Cleo backend did not respond. See ~/.cleo/cleo.log");
      });

      app.handle().plugin(
        tauri_plugin_log::Builder::default()
          .level(log::LevelFilter::Info)
          .build(),
      )?;
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app, event| {
      match event {
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
          if let Some(state) = app.try_state::<Backend>() {
            if let Some(child) = state.0.lock().unwrap().take() {
              stop_backend(child);
            }
          }
        }
        RunEvent::WindowEvent { event: tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed, .. } => {
          if let Some(state) = app.try_state::<Backend>() {
            if let Some(child) = state.0.lock().unwrap().take() {
              stop_backend(child);
            }
          }
        }
        _ => {}
      }
    });
}

/// Async sleep without pulling in a direct tokio dependency (tauri's runtime re-exports it).
async fn tokio_sleep(ms: u64) {
  tauri::async_runtime::spawn_blocking(move || std::thread::sleep(Duration::from_millis(ms)))
    .await
    .ok();
}
