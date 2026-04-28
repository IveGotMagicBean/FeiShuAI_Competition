// Sentinel-MCP desktop shell
//
// 现在做了什么：
//   - setup() 阶段 spawn sidecar 二进制 sentinel-mcp-server（PyInstaller --onefile 打的），
//     它在 8766 端口起 PWA dashboard。Tauri 主窗口直接加载这个 URL。
//   - 系统托盘图标 + 菜单：「打开 Dashboard」「最小化」「关于」「退出」
//   - 关闭主窗口 → 隐藏到托盘，不退出进程（桌面 app 标准行为）
//   - 退出时 kill sidecar 子进程，不留僵尸
//
// 设计要点：
//   - sidecar 通过 capabilities/default.json 显式 allowlist；其他任意 shell 命令仍被拒
//   - SENTINEL_PORT / SENTINEL_DB 等 env 由 sidecar 自己读默认值
//   - sidecar stdout/stderr 通过 CommandEvent 转到主进程 log（CI 调试用）

use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, RunEvent, State, WindowEvent,
};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[derive(Default)]
struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .setup(|app| {
            // ---- 1) 启动 sidecar (sentinel-mcp-server) ----
            let sidecar = app
                .shell()
                .sidecar("sentinel-mcp-server")
                .expect("sidecar binary 'sentinel-mcp-server' not found in bundle");
            let (mut rx, child) = sidecar.spawn().expect("failed to spawn sentinel-mcp-server");

            // 把子进程 stdout/stderr 转成主进程 println!，至少在 dev 模式下能看到 uvicorn log
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_shell::process::CommandEvent;
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            eprintln!("[sidecar] {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[sidecar] {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Error(err) => {
                            eprintln!("[sidecar] error: {}", err);
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[sidecar] terminated: code={:?}", payload.code);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            // 把 child 句柄存进 state，退出时 kill
            let state: State<SidecarState> = app.state();
            *state.0.lock().unwrap() = Some(child);

            // ---- 2) 托盘菜单 ----
            let open_dash = MenuItem::with_id(app, "open_dash", "打开 Dashboard", true, None::<&str>)?;
            let hide      = MenuItem::with_id(app, "hide",      "最小化到托盘", true, None::<&str>)?;
            let sep1      = PredefinedMenuItem::separator(app)?;
            let about     = MenuItem::with_id(app, "about",     "关于 Sentinel-MCP", true, None::<&str>)?;
            let quit      = MenuItem::with_id(app, "quit",      "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_dash, &hide, &sep1, &about, &quit])?;

            let _tray = TrayIconBuilder::with_id("sentinel-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .icon_as_template(true)
                .tooltip("Sentinel-MCP — guarding tool calls")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open_dash" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.unminimize();
                            let _ = w.set_focus();
                        }
                    }
                    "hide" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                    "about" => {
                        let _ = app.emit("about-clicked", "v0.2.0");
                    }
                    "quit" => {
                        // 在退出前 kill sidecar
                        let s: State<SidecarState> = app.state();
                        if let Some(child) = s.0.lock().unwrap().take() {
                            let _ = child.kill();
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        // 关闭窗口 → 隐藏到托盘（桌面 app 标准行为）
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { api, .. } => {
                api.prevent_exit();
            }
            RunEvent::Exit => {
                // 进程真正要退出时（quit 菜单）— 兜底再 kill 一遍 sidecar
                let s: State<SidecarState> = app_handle.state();
                if let Some(child) = s.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
            _ => {}
        });
}
