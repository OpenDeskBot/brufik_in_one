"""miot-ctl 命令行入口。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import sys
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import parse_qs, urlparse

import click

from miot_ctl.session import MiotSession
from miot_ctl.util import code_msg, infer_value, print_json

T = TypeVar("T")


def _run(fn: Callable[[MiotSession], Awaitable[T]]) -> T:
    async def _wrapper() -> T:
        session = MiotSession()
        try:
            return await fn(session)
        finally:
            await session.close()

    return asyncio.run(_wrapper())


def _parse_auth_payload(payload: str) -> tuple[str, str]:
    """解析授权信息：支持 base64 JSON、完整回调 URL、或仅 query 字符串。"""
    payload = payload.strip().strip('"').strip("'")
    if not payload:
        raise click.ClickException("授权信息为空")

    # 回调 URL 或 ?code=...&state=...
    if "code=" in payload and "state=" in payload:
        query = urlparse(payload).query if "://" in payload else payload.lstrip("?")
        params = parse_qs(query)
        code = (params.get("code") or [""])[0].strip()
        state = (params.get("state") or [""])[0].strip()
        if code and state:
            return code, state

    # base64({"code":"...","state":"..."})
    try:
        decoded = base64.b64decode(payload).decode("utf-8")
        data = json.loads(decoded)
        code = data["code"].strip()
        state = data["state"].strip()
        if code and state:
            return code, state
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        AttributeError,
    ):
        pass

    raise click.ClickException(
        "授权信息格式错误。请粘贴浏览器地址栏完整 URL，或回调页的 base64 内容"
    )


def _annotate_results(results: list[dict] | dict) -> None:
    items = results if isinstance(results, list) else [results]
    for item in items:
        if not isinstance(item, dict):
            continue
        msg = code_msg(item.get("code"))
        if msg:
            item["code_msg"] = msg


@click.group()
@click.version_option(package_name="miot_ctl")
def main() -> None:
    """轻量米家 IoT 命令行工具（基于 miloco-miot SDK）。"""


@main.group("auth")
def auth_group() -> None:
    """小米账号绑定。"""


@auth_group.command("bind")
@click.option("--no-wait", is_flag=True, help="只打印授权 URL，不等待输入")
def auth_bind(no_wait: bool) -> None:
    """生成 OAuth 授权链接。"""
    url = _run(lambda s: s.bind_url())
    click.echo("\n请在浏览器打开以下链接完成小米账号授权:\n")
    click.echo(f"  {url}\n")
    click.echo(
        "提示: 授权后会跳转到「小米账号授权完成」页面，\n"
        "      点击「复制授权码」后粘贴到下方（也可粘贴完整回调 URL）。\n"
    )
    if no_wait:
        click.echo("授权完成后运行:")
        click.echo('  miot-ctl auth authorize "<授权码或回调URL>"')
        return
    payload = click.prompt("粘贴浏览器地址栏的完整回调 URL", type=str)
    code, state = _parse_auth_payload(payload)

    async def _authorize(s: MiotSession):
        return await s.authorize(code, state)

    info = _run(_authorize)
    click.echo("绑定成功")
    if info.user_info:
        click.echo(f"用户: {info.user_info.nickname}")


@auth_group.command("authorize")
@click.argument("payload", required=False, default=None)
@click.option("--code", default=None, help="OAuth code")
@click.option("--state", default=None, help="OAuth state")
def auth_authorize(payload: str | None, code: str | None, state: str | None) -> None:
    """提交授权信息完成绑定。支持回调 URL / base64 / --code --state。"""
    if code and state:
        auth_code, auth_state = code.strip(), state.strip()
    elif payload:
        auth_code, auth_state = _parse_auth_payload(payload)
    else:
        raise click.ClickException("请提供回调 URL，或使用 --code 与 --state")

    async def _authorize(s: MiotSession):
        return await s.authorize(auth_code, auth_state)

    info = _run(_authorize)
    click.echo("绑定成功")
    if info.user_info:
        click.echo(f"用户: {info.user_info.nickname}")


@auth_group.command("status")
def auth_status() -> None:
    """查看绑定状态。"""
    print_json(_run(lambda s: s.status()))


@auth_group.command("unbind")
def auth_unbind() -> None:
    """解绑账号。"""
    _run(lambda s: s.unbind())
    click.echo("已解绑")


@main.group("device")
def device_group() -> None:
    """设备操作。"""


@device_group.command("list")
@click.option("--online", is_flag=True, help="只显示在线设备")
@click.option("--json", "as_json", is_flag=True, help="JSON 输出")
def device_list(online: bool, as_json: bool) -> None:
    """列出设备。"""
    rows = _run(lambda s: s.list_devices())
    if online:
        rows = [r for r in rows if r.get("online")]
    if as_json:
        print_json(rows)
        return
    click.echo("did|name|room|online|model")
    for r in rows:
        click.echo(
            f"{r['did']}|{r['name']}|{r.get('room_name') or ''}|"
            f"{r.get('online')}|{r.get('model') or ''}"
        )


@device_group.command("get")
@click.argument("did")
def device_get(did: str) -> None:
    """查看设备详情。"""
    print_json(_run(lambda s: s.get_device(did)))


@device_group.command("spec")
@click.argument("did")
def device_spec(did: str) -> None:
    """查看设备能力（属性/动作）。"""
    spec = _run(lambda s: s.get_spec(did))
    click.echo("type_name|api_iid|rw|description")
    for item in spec.values():
        rw = []
        if item["readable"]:
            rw.append("r")
        if item["writable"]:
            rw.append("w")
        click.echo(
            f"{item.get('type_name') or ''}|{item['api_iid']}|"
            f"{''.join(rw)}|{item['description']}"
        )


@device_group.command("set")
@click.argument("did")
@click.argument("key")
@click.argument("value")
def device_set(did: str, key: str, value: str) -> None:
    """设置设备属性。key 可用 type_name（如 on/brightness）或 prop.siid.piid。"""
    val = infer_value(value)
    results = _run(lambda s: s.set_property(did, key, val))
    _annotate_results(results)
    print_json({"did": did, "results": results})


@device_group.command("props")
@click.argument("did")
@click.argument("keys", nargs=-1)
def device_props(did: str, keys: tuple[str, ...]) -> None:
    """读取设备属性。不传 keys 则读全部可读属性。"""
    key_list = list(keys) or None
    results = _run(lambda s: s.get_properties(did, key_list))
    _annotate_results(results)
    print_json({"did": did, "properties": results})


@device_group.command("action")
@click.argument("did")
@click.argument("key")
@click.argument("args", nargs=-1)
def device_action(did: str, key: str, args: tuple[str, ...]) -> None:
    """调用设备动作。例: miot-ctl device action <did> play-text \"你好\" """
    parsed = [infer_value(a) for a in args]

    async def _call(s: MiotSession):
        return await s.call_action(did, key, parsed)

    result = _run(_call)
    _annotate_results(result)
    print_json({"did": did, "result": result})


@main.group("scene")
def scene_group() -> None:
    """米家场景。"""


@scene_group.command("list")
def scene_list() -> None:
    """列出手动场景。"""
    rows = _run(lambda s: s.list_scenes())
    click.echo("scene_id|scene_name|home|room")
    for r in rows:
        click.echo(
            f"{r['scene_id']}|{r['scene_name']}|"
            f"{r.get('home_name') or ''}|{r.get('room_name') or ''}"
        )


@scene_group.command("run")
@click.argument("scene_id")
def scene_run(scene_id: str) -> None:
    """触发场景。"""
    result = _run(lambda s: s.run_scene(scene_id))
    print_json({"scene_id": scene_id, "result": result})


if __name__ == "__main__":
    try:
        main()
    except click.ClickException:
        raise
    except Exception as exc:
        click.echo(f"错误: {exc}", err=True)
        sys.exit(1)
