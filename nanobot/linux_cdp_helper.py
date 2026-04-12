"""Linux helper that launches or reuses a host Chrome CDP window."""

from __future__ import annotations

import json
import sys

try:
    from nanobot import _non_macos_cdp_helper as _common
except Exception:  # pragma: no cover - used by installed standalone helper copies
    import _non_macos_cdp_helper as _common  # type: ignore[no-redef]

DEFAULT_HELPER_HOST = _common.DEFAULT_HELPER_HOST
DEFAULT_HELPER_PORT = _common.DEFAULT_HELPER_PORT
DEFAULT_HELPER_URL = _common.DEFAULT_HELPER_URL
DEFAULT_START_URL = _common.DEFAULT_START_URL
HELPER_LABEL = _common.LINUX_HELPER_LABEL


def helper_install_dir():
    return _common.helper_install_dir("linux")


def helper_python_script_path():
    return _common.helper_python_script_path("linux")


def helper_launcher_script_path():
    return _common.helper_launcher_script_path("linux")


def helper_stdout_log_path():
    return _common.helper_stdout_log_path("linux")


def helper_stderr_log_path():
    return _common.helper_stderr_log_path("linux")


def helper_token_path():
    return _common.helper_token_path("linux")


def linux_systemd_service_path():
    return _common.linux_systemd_service_path()


def linux_autostart_path():
    return _common.linux_autostart_path()


def load_helper_token() -> str:
    return _common.load_helper_token("linux")


def write_helper_token(token: str):
    return _common.write_helper_token(token, platform_name="linux")


def load_or_create_helper_token() -> str:
    return _common.load_or_create_helper_token("linux")


def resolve_chrome_path(configured_path: str = ""):
    return _common.resolve_chrome_path(configured_path, platform_name="linux")


def request_helper_health(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 1.0) -> bool:
    return _common.request_helper_health(helper_url, timeout_s=timeout_s)


def request_helper_ensure(
    helper_url: str,
    *,
    endpoint_url: str,
    profile_dir: str,
    start_url: str = DEFAULT_START_URL,
    chrome_path: str = "",
    force_new_window: bool = False,
    helper_token: str = "",
    timeout_s: float = 20.0,
) -> dict[str, str]:
    return _common.request_helper_ensure(
        helper_url,
        endpoint_url=endpoint_url,
        profile_dir=profile_dir,
        start_url=start_url,
        chrome_path=chrome_path,
        force_new_window=force_new_window,
        helper_token=helper_token,
        timeout_s=timeout_s,
    )


def ensure_cdp_browser(
    *,
    endpoint_url: str,
    profile_dir: str,
    start_url: str = DEFAULT_START_URL,
    chrome_path: str = "",
    force_new_window: bool = False,
    timeout_s: float = 15.0,
) -> dict[str, str]:
    return _common.ensure_cdp_browser(
        endpoint_url=endpoint_url,
        profile_dir=profile_dir,
        platform_name="linux",
        start_url=start_url,
        chrome_path=chrome_path,
        force_new_window=force_new_window,
        timeout_s=timeout_s,
    )


def serve(
    *,
    host: str | None = None,
    port: int = DEFAULT_HELPER_PORT,
    helper_token: str = "",
    token_file: str = "",
) -> None:
    _common.serve(
        platform_name="linux",
        host=host,
        port=port,
        helper_token=helper_token,
        token_file=token_file,
    )


def wait_for_helper(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 5.0) -> bool:
    return _common.wait_for_helper(helper_url, timeout_s=timeout_s)


def install_linux_helper(helper_token: str = "") -> dict[str, str]:
    return _common.install_linux_helper(
        helper_module_file=__file__,
        shared_module_file=_common.__file__,
        helper_token=helper_token,
    )


def build_cli_parser():
    return _common.build_cli_parser("Nanobot Linux host CDP helper")


def main(argv: list[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        try:
            result = install_linux_helper(helper_token=str(args.helper_token or "").strip())
        except Exception as exc:
            print(f"Failed to install the Linux host CDP helper: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Installed Linux host CDP helper at {result['helper_url']}")
            print(f"Service file: {result['service_file']}")
            print(f"Autostart file: {result['autostart_file']}")
            print(f"Helper script: {result['helper_script']}")
        return 0

    if args.command == "health":
        healthy = request_helper_health(args.helper_url, timeout_s=0.5)
        if healthy:
            print(f"Linux host CDP helper is healthy at {args.helper_url}")
            return 0
        print(f"Linux host CDP helper is not reachable at {args.helper_url}", file=sys.stderr)
        return 1

    if args.command == "serve":
        serve(
            host=args.host,
            port=args.port,
            helper_token=args.helper_token,
            token_file=args.token_file,
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
