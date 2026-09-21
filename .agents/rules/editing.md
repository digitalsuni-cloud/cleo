## Editing Practices

- **Never** use scripts (like `sed`, `awk`, or custom Python scripts via `run_command`) to make bulk edits or text replacements in files. 
- Always use the native `replace_file_content` tool to edit code line by line to ensure precision, avoid accidental deletions, and ensure the changes are properly tracked in the diff output.
