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

/// Asks the sidecar to exit cleanly (SIGTERM is forwarded by the PyInstaller bootloader to the
/// Python process, which unloads local models), then force-kills whatever is left.
fn stop_backend(child: CommandChild) {
  #[cfg(unix)]
  {
    let _ = std::process::Command::new("kill")
      .arg("-TERM")
      .arg(child.pid().to_string())
      .status();
    std::thread::sleep(Duration::from_millis(1500));
  }
  let _ = child.kill();
}

#[cfg(target_os = "windows")]
fn ensure_windows_sidecar() {
  if let Ok(current_exe) = std::env::current_exe() {
    if let Some(exe_dir) = current_exe.parent() {
      let target_sidecar = exe_dir.join("cleo-server-x86_64-pc-windows-msvc.exe");
      let alt_sidecar = exe_dir.join("cleo-server.exe");
      if !target_sidecar.exists() && !alt_sidecar.exists() {
        #[cfg(not(debug_assertions))]
        {
          const SIDECAR_BYTES: &[u8] = include_bytes!("../binaries/cleo-server-x86_64-pc-windows-msvc.exe");
          let _ = std::fs::write(&target_sidecar, SIDECAR_BYTES);
        }
      }
    }
  }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  #[cfg(target_os = "linux")]
  std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");

  #[cfg(target_os = "windows")]
  ensure_windows_sidecar();

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
          let spawned = handle
            .shell()
            .sidecar("cleo-server")
            .map(|c| {
              c.env("PORT", BACKEND_PORT.to_string())
                .env("CLEO_NO_BROWSER", "1")
                .env("CLEO_NO_UPDATE", "1")
            })
            .and_then(|c| c.spawn());

          match spawned {
            Ok((mut rx, child)) => {
              log::info!("Cleo Python sidecar spawned (PID: {:?})", child.pid());
              if let Some(state) = handle.try_state::<Backend>() {
                *state.0.lock().unwrap() = Some(child);
              }

              let h = handle.clone();
              tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                  match event {
                    CommandEvent::Stdout(line) => print!("[Backend] {}", String::from_utf8_lossy(&line)),
                    CommandEvent::Stderr(line) => eprint!("[Backend ERR] {}", String::from_utf8_lossy(&line)),
                    CommandEvent::Terminated(payload) => {
                      log::warn!("[Backend] Process terminated: {:?}", payload);
                      splash_status(
                        &h,
                        &format!(
                          "The Cleo backend stopped unexpectedly (exit code {:?}). See ~/.cleo/cleo.log",
                          payload.code
                        ),
                      );
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

      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app, event| {
      if let RunEvent::Exit = event {
        if let Some(state) = app.try_state::<Backend>() {
          if let Some(child) = state.0.lock().unwrap().take() {
            stop_backend(child);
          }
        }
      }
    });
}

/// Async sleep without pulling in a direct tokio dependency (tauri's runtime re-exports it).
async fn tokio_sleep(ms: u64) {
  tauri::async_runtime::spawn_blocking(move || std::thread::sleep(Duration::from_millis(ms)))
    .await
    .ok();
}
