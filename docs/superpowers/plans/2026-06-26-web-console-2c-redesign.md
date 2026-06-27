# Web 控制台 2C 化改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动核心管线/协议/固件的前提下，为 deskbot-server 增加一套消费级（2C）Web 外壳，把「表情」「声音」做成两个核心可玩页面，并把记忆/提醒/人/设备重新包装、把开发者功能收进「高级」。

**Architecture:** 纯前端为主。新增一个 `app2c_bp` 蓝图（仅渲染模板）+ 消费级母版 `base_2c.html` + 主题 `theme_2c.css`，页面用内联 Vue 调用**已存在**的 JSON 接口（`/api/doubao_tts/*`、`/api/face_expr_scenes`、`/app/api/*`）。后端 Phase 1 零新增；Phase 2 可选新增一个极小的 per-device 情绪→场景映射 store。

**Tech Stack:** Flask 3 + Jinja2 + 内联 Vue 3（CDN/vendored）+ 原生 CSS；图标内联 Lucide SVG；pytest。

参考来源：
- 设计 spec：`docs/superpowers/specs/2026-06-26-web-console-2c-redesign-design.md`
- 视觉/标记来源（真实可复制）：`web控制台-2C-原型.html`（仓库根目录）
- 既有测试风格：`service/deskbot-server/tests/test_endpoint_auth.py`

> 所有路径以 `service/deskbot-server/` 为根，除非另写明（如根目录的原型 HTML）。

---

## File Structure

新建：
- `src/deskbot_server/web/static/theme_2c.css` — 消费级主题（从原型 `<style>` 移植）。
- `src/deskbot_server/web/templates/base_2c.html` — 侧边栏外壳 + Lucide 图标宏 + `{% block content %}`。
- `src/deskbot_server/web/templates/app2c/home.html` — 首页「家」。
- `src/deskbot_server/web/templates/app2c/voice.html` — 声音（调既有 `/api/doubao_tts/*`）。
- `src/deskbot_server/web/templates/app2c/expr.html` — 表情（调既有 `/api/face_expr_scenes`）。
- `src/deskbot_server/web/templates/app2c/memories.html` — 它记得的事（调 `/app/api/memories`）。
- `src/deskbot_server/web/templates/app2c/reminders.html` — 提醒（调 `/app/api/scheduled-tasks`）。
- `src/deskbot_server/web/templates/app2c/people.html` — 认识的人（调 `/app/api/face-profiles`）。
- `src/deskbot_server/web/templates/app2c/devices.html` — 我的设备（调 `/app/api/devices`）。
- `src/deskbot_server/web/templates/app2c/advanced.html` — 高级 hub（链接既有页面）。
- `src/deskbot_server/web/blueprints/app2c_bp.py` — 仅渲染上述模板的蓝图。
- `tests/test_app2c_pages.py` — 路由鉴权 + 200 烟雾测试。

修改：
- `src/deskbot_server/web/app.py` — 注册 `app2c_bp`；登录后落地页指向 `/home`。

Phase 2（可选）新建：
- `src/deskbot_server/emotion_expr_map_store.py` — per-device 情绪→场景映射 store。
- `tests/test_emotion_expr_map_store.py` — store 单测。
- 在 `app2c_bp.py` 增加 `GET/POST /api/emotion_expr_map`。
- `tests/test_emotion_expr_map_api.py` — 接口测试。

---

## Phase 0 — 外壳与蓝图

### Task 1: 消费级主题 theme_2c.css

**Files:**
- Create: `src/deskbot_server/web/static/theme_2c.css`

- [ ] **Step 1: 从原型移植样式**

打开仓库根目录 `web控制台-2C-原型.html`，复制其 `<style>…</style>` 标签**内部**的全部 CSS，原样粘贴到 `theme_2c.css`。该 CSS 已包含 `.app/.side/.navi/.stage/.hero/.mini/.vgrid/.vcard/.maprow/.thumb/.toast` 等全部类，且使用 `:root` 变量，无需改动。

- [ ] **Step 2: 校验文件非空且含关键类**

Run: `grep -c -E '\.side|\.navi|\.vgrid|\.thumb' src/deskbot_server/web/static/theme_2c.css`
Expected: 输出 ≥ 4

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/static/theme_2c.css
git commit -m "feat(web2c): add consumer theme_2c.css ported from prototype"
```

---

### Task 2: 母版 base_2c.html（侧边栏 + Lucide 图标宏）

**Files:**
- Create: `src/deskbot_server/web/templates/base_2c.html`

- [ ] **Step 1: 写母版骨架**

创建 `base_2c.html`，内容如下（侧边栏导航 + 图标宏 + 内容块）。导航项的 `active_nav` 由各页传入。Lucide SVG 路径与原型一致（可从原型 `<nav class="navi">` 与图标处复制；下面已内联常用项）。

```html
{% macro icon(name) -%}
{% if name == 'home' %}<svg viewBox="0 0 24 24"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>
{% elif name == 'expr' %}<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14c1 1.3 6 1.3 7 0"/><circle cx="9" cy="10" r="1"/><circle cx="15" cy="10" r="1"/></svg>
{% elif name == 'voice' %}<svg viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M16 9a4 4 0 010 6"/><path d="M19 6a8 8 0 010 12"/></svg>
{% elif name == 'memory' %}<svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 011 1v16l-7-4-7 4V4a1 1 0 011-1z"/></svg>
{% elif name == 'remind' %}<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
{% elif name == 'people' %}<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-6 8-6s8 2 8 6"/></svg>
{% elif name == 'device' %}<svg viewBox="0 0 24 24"><rect x="6" y="3" width="12" height="18" rx="2"/><path d="M11 18h2"/></svg>
{% elif name == 'advanced' %}<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 13a7 7 0 000-2l2-1.5-2-3.4-2.3 1a7 7 0 00-1.7-1L15 3h-4l-.7 2.6a7 7 0 00-1.7 1l-2.3-1-2 3.4L4.6 11a7 7 0 000 2l-2 1.5 2 3.4 2.3-1a7 7 0 001.7 1L11 21h4l.7-2.6a7 7 0 001.7-1l2.3 1 2-3.4z"/></svg>
{% endif %}
{%- endmacro %}
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block page_title %}小歪 · 我的桌搭子{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='theme_2c.css') }}">
  {% block extra_head %}{% endblock %}
</head>
<body>
<div class="app">
  <aside class="side">
    <div class="brand"><div class="logo">歪</div><div><b>小歪</b><small>我的桌搭子</small></div></div>
    <nav class="navi">
      <a href="{{ url_for('app2c.home') }}" class="navlink {{ 'on' if active_nav=='home' }}">{{ icon('home') }}家</a>
      <div class="lbl">核心玩法</div>
      <a href="{{ url_for('app2c.expr') }}" class="navlink {{ 'on' if active_nav=='expr' }}">{{ icon('expr') }}表情</a>
      <a href="{{ url_for('app2c.voice') }}" class="navlink {{ 'on' if active_nav=='voice' }}">{{ icon('voice') }}声音</a>
      <div class="lbl">日常</div>
      <a href="{{ url_for('app2c.memories') }}" class="navlink {{ 'on' if active_nav=='memory' }}">{{ icon('memory') }}它记得的事</a>
      <a href="{{ url_for('app2c.reminders') }}" class="navlink {{ 'on' if active_nav=='remind' }}">{{ icon('remind') }}提醒</a>
      <a href="{{ url_for('app2c.people') }}" class="navlink {{ 'on' if active_nav=='people' }}">{{ icon('people') }}认识的人</a>
      <a href="{{ url_for('app2c.devices') }}" class="navlink {{ 'on' if active_nav=='device' }}">{{ icon('device') }}我的设备</a>
      <div class="gap"></div>
      <a href="{{ url_for('app2c.advanced') }}" class="navlink {{ 'on' if active_nav=='advanced' }}">{{ icon('advanced') }}高级</a>
    </nav>
    <div class="foot"><div class="userchip"><div class="ua">歪</div><div><b>{{ nav_display_name or '我' }}</b><small>{{ nav_user_email or '' }}</small></div></div></div>
  </aside>
  <main>{% block content %}{% endblock %}</main>
</div>
{% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 2: 在 theme_2c.css 末尾补 `.navlink` 样式**（原型用 `<button>`，母版用 `<a>`）

把原型中 `.navi button` 的样式追加一份 `.navi .navlink` 等价规则到 `theme_2c.css` 末尾：

```css
.navi .navlink{display:flex;align-items:center;gap:11px;text-decoration:none;
  padding:10px 12px;border-radius:11px;font-size:14px;color:var(--dim);font-weight:600}
.navi .navlink:hover{background:#f6f6fc;color:var(--ink)}
.navi .navlink.on{background:var(--brand-soft);color:var(--brand)}
.navi .navlink svg{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}
```

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/base_2c.html src/deskbot_server/web/static/theme_2c.css
git commit -m "feat(web2c): add base_2c shell with sidebar + lucide icon macro"
```

---

### Task 3: app2c 蓝图 + 注册 + 登录落地页

**Files:**
- Create: `src/deskbot_server/web/blueprints/app2c_bp.py`
- Modify: `src/deskbot_server/web/app.py`
- Test: `tests/test_app2c_pages.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_app2c_pages.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DESKBOT_DB_PATH", str(db_path))
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(db_path)
        init_database()
        yield db_path


def _login_client():
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    create_user("u2c@example.com", "password1234")
    app = create_app()
    client = app.test_client()
    client.post("/login", data={"email": "u2c@example.com", "password": "password1234"})
    return client


PAGES = ["/home", "/voice", "/expr", "/my/memories", "/my/reminders", "/my/people", "/my/devices", "/advanced"]


def test_2c_pages_redirect_when_anonymous(temp_db):
    from deskbot_server.web.app import create_app

    client = create_app().test_client()
    assert client.get("/home").status_code == 302


@pytest.mark.parametrize("path", PAGES)
def test_2c_pages_render_when_logged_in(temp_db, path):
    resp = _login_client().get(path)
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行确认失败**

Run: `cd service/deskbot-server && python -m pytest tests/test_app2c_pages.py -q`
Expected: FAIL（路由不存在 → 404/302 不匹配，或 import 错误）

- [ ] **Step 3: 写蓝图**

```python
# src/deskbot_server/web/blueprints/app2c_bp.py
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("app2c", __name__)


@bp.get("/home")
@login_required
def home():
    return render_template("app2c/home.html", active_nav="home")


@bp.get("/voice")
@login_required
def voice():
    return render_template("app2c/voice.html", active_nav="voice")


@bp.get("/expr")
@login_required
def expr():
    return render_template("app2c/expr.html", active_nav="expr")


@bp.get("/my/memories")
@login_required
def memories():
    return render_template("app2c/memories.html", active_nav="memory")


@bp.get("/my/reminders")
@login_required
def reminders():
    return render_template("app2c/reminders.html", active_nav="remind")


@bp.get("/my/people")
@login_required
def people():
    return render_template("app2c/people.html", active_nav="people")


@bp.get("/my/devices")
@login_required
def devices():
    return render_template("app2c/devices.html", active_nav="device")


@bp.get("/advanced")
@login_required
def advanced():
    return render_template("app2c/advanced.html", active_nav="advanced")
```

- [ ] **Step 4: 创建 8 个最小模板占位**（让路由先能渲染，内容后续任务填充）

为 `templates/app2c/` 下 8 个文件各写最小骨架，例如 `home.html`：

```html
{% extends "base_2c.html" %}
{% block content %}<div class="pagehd"><div><h1>家</h1></div></div>{% endblock %}
```

其余 7 个同样 `{% extends "base_2c.html" %}` + 一个 `<h1>`（标题分别为 表情/声音/它记得的事/提醒/认识的人/我的设备/高级），`active_nav` 已由路由传入。

- [ ] **Step 5: 注册蓝图并把登录落地页指向 /home**

修改 `src/deskbot_server/web/app.py`：

在 `app.register_blueprint(app_bp)` 之后新增一行：

```python
    from deskbot_server.web.blueprints.app2c_bp import bp as app2c_bp
    app.register_blueprint(app2c_bp)
```

并把 `login_manager.login_view` 后续登录成功的跳转指向 `/home`：在 `auth_bp` 登录成功默认 `next` 为 `/home`。**最小改动**：在 `app.py` 的 `require_auth` 中，已登录用户访问 `/` 时重定向到 `app2c.home`：

```python
        if path == "/" and current_user.is_authenticated:
            return redirect(url_for("app2c.home"))
```

（放在 `require_auth` 内、`if path == "/" or path.startswith(...)` 判断**之前**。）

- [ ] **Step 6: 运行测试确认通过**

Run: `cd service/deskbot-server && python -m pytest tests/test_app2c_pages.py -q`
Expected: PASS（9 个用例）

- [ ] **Step 7: Commit**

```bash
git add src/deskbot_server/web/blueprints/app2c_bp.py src/deskbot_server/web/app.py \
        src/deskbot_server/web/templates/app2c/ tests/test_app2c_pages.py
git commit -m "feat(web2c): add app2c blueprint, 8 page routes, smoke tests"
```

---

## Phase 1 — 页面接线（调用既有接口）

> 各页 markup 以根目录 `web控制台-2C-原型.html` 对应 `<section>` 为视觉来源：把该 section 的内部 HTML 放进对应模板的 `{% block content %}`，再按下方把"写死的数据"换成 Vue 拉取既有接口。每个任务完成后手动验收：登录后访问页面、按描述交互。

### Task 4: 首页「家」

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/home.html`

- [ ] **Step 1: 移植首页 markup**

把原型 `<section class="screen on" id="home">` 的**内部** HTML 复制进 `home.html` 的 `{% block content %}`。把卡片上的 `onclick="go('xxx')"` 改成真实链接：用 `<a href="{{ url_for('app2c.voice') }}">` 等包裹英雄卡/日常卡，或改 `onclick="location.href='…'"`。

- [ ] **Step 2: 手动验收**

启动 web：`cd service/deskbot-server && DESKBOT_WEB_PORT=5050 python -m deskbot_server.web`，登录后访问 `/home`：应见机器人大脸 + 两张英雄卡 + 四个日常入口，点击可跳到对应页面。

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/home.html
git commit -m "feat(web2c): home page with hero + daily entries"
```

---

### Task 5: 声音页（调 /api/doubao_tts/*）

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/voice.html`

接口契约（已存在，见 spec §3）：
- `GET /api/doubao_tts/speakers` → `{ok, speakers:[{label,id,scene,resource_id}]}`
- `GET /api/doubao_tts/config` → `{ok, config:{speaker, ...}}`
- `POST /api/doubao_tts/config` body `{speaker, resource_id}` → 设当前音色
- `POST /api/doubao_tts/synthesize` body `{text, speaker, resource_id}` → `{ok, wav_base64, sample_rate}`

- [ ] **Step 1: 写页面 + Vue 接线**

`voice.html`：

```html
{% extends "base_2c.html" %}
{% block page_title %}声音 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>声音</h1><p>挑一个喜欢的音色，点试听，满意就设为当前。</p></div></div>
<div id="app">
  <div v-for="(list, scene) in grouped" :key="scene">
    <div class="scene-h">[[ scene ]]</div>
    <div class="vgrid">
      <div class="vcard" :class="{cur: v.id===current}" v-for="v in list" :key="v.id">
        <span class="curtag" v-if="v.id===current">当前</span>
        <b>[[ v.label ]]</b><small>[[ v.scene ]]</small>
        <div class="vbtns">
          <button class="vbtn play" @click="preview(v)">[[ playing===v.id ? '播放中…' : '▶ 试听' ]]</button>
          <button class="vbtn set" @click="setCurrent(v)">[[ v.id===current ? '使用中' : '设为当前' ]]</button>
        </div>
      </div>
    </div>
  </div>
  <div class="slider-card">
    <div class="t"><span>试听音量</span><span>[[ vol ]]</span></div>
    <input type="range" min="0" max="100" v-model.number="vol">
  </div>
  <p class="lead" v-if="msg">[[ msg ]]</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  data(){ return { speakers:[], current:'', playing:'', vol:80, msg:'' }; },
  computed:{
    grouped(){ const g={}; for(const v of this.speakers){ (g[v.scene||'其他'] ||= []).push(v); } return g; }
  },
  methods:{
    async load(){
      const s = await (await fetch('/api/doubao_tts/speakers')).json();
      this.speakers = s.speakers || [];
      const c = await (await fetch('/api/doubao_tts/config')).json();
      this.current = (c.config && c.config.speaker) || '';
    },
    async setCurrent(v){
      const r = await (await fetch('/api/doubao_tts/config', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({speaker:v.id, resource_id:v.resource_id})})).json();
      if(r.ok){ this.current = v.id; this.msg = '已设为当前音色：'+v.label; }
      else { this.msg = '设置失败：'+(r.error||''); }
    },
    async preview(v){
      this.playing = v.id; this.msg='';
      try{
        const r = await (await fetch('/api/doubao_tts/synthesize', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({text:'你好，我是你的桌搭子小歪。', speaker:v.id, resource_id:v.resource_id})})).json();
        if(r.ok && r.wav_base64){ const a=new Audio('data:audio/wav;base64,'+r.wav_base64); a.volume=this.vol/100; a.play(); }
        else { this.msg='试听失败：'+(r.error||''); }
      } finally { this.playing=''; }
    },
  },
  mounted(){ this.load(); },
}).mount('#app');
</script>
{% endblock %}
```

> 说明：`vue.global.prod.min.js` 已存在于 `static/vendor/`。用 `[[ ]]` 分隔符避免与 Jinja `{{ }}` 冲突。
> 音量：本任务的滑杆控制**试听播放音量**（浏览器端 `Audio.volume`），真实可用。设备「说话音量」的持久化目前无确认的既有写入接口（音量是 LLM 每轮 JSON 字段 / 设备侧），故不在本期内伪造；若后续需要，再确认/新增一个持久化音量接口（与 §7.2 同样的薄接口方式）。

- [ ] **Step 2: 手动验收**

登录后访问 `/voice`：音色按场景分组显示；点「▶ 试听」能听到声音（需 TTS 服务在线 + 已配 `DOUBAO_TTS_API_KEY`）；点「设为当前」后该卡显示「当前」。

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/voice.html
git commit -m "feat(web2c): voice page (gallery + preview + set-current) via existing endpoints"
```

---

### Task 6: 表情页（调 /api/face_expr_scenes）

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/expr.html`

接口契约（已实测确认）：
- `GET /api/face_expr_scenes?device_id=<id>` → `{ok, config:[{name,title,frames:[{ms,elements}]}], exists, file, device_id}`（场景在 **`config`** 键下）
- `POST /api/face_expr_scenes` body `{device_id, scenes:[…]}` → 保存（端点用 `_effective_device_id` 从 body 读 `device_id`，用 `normalize_face_expr_scenes` 从 body 读 `scenes`，两者均兼容）
- 当前设备：`GET /app/api/devices` → `{ok, current_device_id}`

- [ ] **Step 1: 写页面 + Vue（皮肤选择 + 客户端预览 + 保存）**

`expr.html`：

```html
{% extends "base_2c.html" %}
{% block page_title %}表情 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>表情</h1><p>挑一个面孔风格，右侧大屏实时预览。</p></div>
  <button class="btn primary" @click="save" id="saveBtn">保存表情</button></div>
<div id="app">
  <div class="exprgrid">
    <div class="stage" style="min-height:280px">
      <div class="face" style="width:260px;height:200px">
        <svg width="220" height="170" viewBox="0 0 120 80" v-html="previewSvg"></svg>
      </div>
    </div>
    <div>
      <div class="blocktitle" style="margin-top:0">面孔风格（场景）</div>
      <div class="thumbs">
        <div class="thumb" :class="{sel: i===selected}" v-for="(s,i) in scenes" :key="s.name"
             @click="selected=i">[[ s.title || s.name ]]</div>
      </div>
    </div>
  </div>
  <p class="lead" v-if="msg">[[ msg ]]</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({
  delimiters:['[[',']]'],
  data(){ return { scenes:[], selected:0, deviceId:'', msg:'' }; },
  computed:{
    previewSvg(){
      const s=this.scenes[this.selected]; if(!s||!s.frames||!s.frames[0]) return '';
      const el=s.frames[0].elements||{}; let out='';
      const draw=(arr,color)=>{ for(const p of (arr||[])){
        if(p.shape&&p.shape.indexOf('ellipse')===0) out+=`<ellipse cx="${p.x}" cy="${p.y}" rx="${p.rw}" ry="${p.rh}" fill="${color}"/>`;
        else if(p.shape==='line') out+=`<line x1="${p.x1}" y1="${p.y1}" x2="${p.x2}" y2="${p.y2}" stroke="${color}" stroke-width="2"/>`;
        else if(p.shape&&p.shape.indexOf('round_rect')===0) out+=`<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="${p.radius||1}" fill="none" stroke="${color}" stroke-width="2"/>`;
      }};
      draw(el.eye_l,'#7fe7ff'); draw(el.eye_r,'#7fe7ff'); draw(el.mouth,'#ff9ec4'); draw(el.extra,'#ffd36b');
      return out;
    }
  },
  methods:{
    async load(){
      const d = await (await fetch('/app/api/devices')).json();
      this.deviceId = d.current_device_id || '';
      if(!this.deviceId){ this.msg='请先在「我的设备」选择一台设备'; return; }
      const r = await (await fetch('/api/face_expr_scenes?device_id='+encodeURIComponent(this.deviceId))).json();
      this.scenes = r.config || [];   // 场景在 config 键下（已实测）
    },
    async save(){
      if(!this.deviceId){ this.msg='没有当前设备，无法保存'; return; }
      const r = await (await fetch('/api/face_expr_scenes', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({device_id:this.deviceId, scenes:this.scenes})})).json();
      this.msg = r.ok ? '表情已保存到这台设备' : ('保存失败：'+(r.error||''));
    },
  },
  mounted(){ this.load(); },
}).mount('#app');
</script>
{% endblock %}
```

> 预览 `viewBox="0 0 120 80"` 与场景图元坐标范围匹配（图元 x≈0–90、y≈0–55）。接口形状已实测，无需再猜。

- [ ] **Step 2: 手动验收**

先在 `/my/devices` 绑定/选中一台设备，再访问 `/expr`：左侧大屏显示选中场景的首帧；点击不同「场景」缩略图，预览实时切换；点「保存表情」提示已保存。用 `GET /api/face_expr_scenes?device_id=…` 复核已写回。

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/expr.html
git commit -m "feat(web2c): expression page (scene picker + client preview + save)"
```

---

### Task 7: 它记得的事（调 /app/api/memories）

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/memories.html`

接口（已存在，用 session 当前设备）：`GET /app/api/memories`、`POST /app/api/memories` `{text}`、`DELETE /app/api/memories/<id>`。

- [ ] **Step 1: 写页面 + Vue（列表/新增/删除）**

```html
{% extends "base_2c.html" %}
{% block page_title %}它记得的事 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>它记得的事</h1><p>机器人记住的关于你的小事，随时可改可删。</p></div></div>
<div id="app">
  <div style="display:flex;gap:8px;margin-bottom:14px;max-width:560px">
    <input v-model="draft" placeholder="加一条它该记住的事…" style="flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:12px">
    <button class="btn primary" @click="add">添加</button>
  </div>
  <div class="colwrap">
    <div class="note" v-for="m in items" :key="m.id">
      <p>[[ m.text ]]</p>
      <small>[[ m.created_at_fmt || '' ]]</small>
      <button class="btn ghost" style="margin-top:8px" @click="del(m)">删除</button>
    </div>
  </div>
  <p class="lead" v-if="msg">[[ msg ]]</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({
  delimiters:['[[',']]'],
  data(){ return { items:[], draft:'', msg:'' }; },
  methods:{
    async load(){ const r=await (await fetch('/app/api/memories')).json();
      if(r.ok) this.items=r.memories||[]; else this.msg=r.error||'加载失败'; },
    async add(){ const t=this.draft.trim(); if(!t) return;
      const r=await (await fetch('/app/api/memories',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})).json();
      if(r.ok){ this.draft=''; this.load(); } else this.msg=r.error||'添加失败'; },
    async del(m){ const r=await (await fetch('/app/api/memories/'+m.id,{method:'DELETE'})).json();
      if(r.ok) this.load(); else this.msg=r.error||'删除失败'; },
  },
  mounted(){ this.load(); },
}).mount('#app');
</script>
{% endblock %}
```

- [ ] **Step 2: 手动验收**：`/my/memories` 能列出、添加、删除记忆（需已选当前设备）。

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/memories.html
git commit -m "feat(web2c): memories page via existing /app/api/memories"
```

---

### Task 8: 提醒 / 认识的人 / 我的设备（只读 + 删除/绑定）

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/reminders.html`
- Modify: `src/deskbot_server/web/templates/app2c/people.html`
- Modify: `src/deskbot_server/web/templates/app2c/devices.html`

接口（已存在）：`GET /app/api/scheduled-tasks` + `DELETE /app/api/scheduled-tasks/<id>`；`GET /app/api/face-profiles` + `DELETE /app/api/face-profiles/<person_id>`；`GET /app/api/devices` + `POST /app/api/devices` `{device_id,display_name}` + `POST /app/api/devices/select` `{device_id}`。

- [ ] **Step 1: reminders.html**

```html
{% extends "base_2c.html" %}
{% block page_title %}提醒 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>提醒</h1><p>它会在这些时间主动叫你。</p></div></div>
<div id="app">
  <div class="tl" v-for="t in tasks" :key="t.id">
    <div class="dot"></div>
    <div class="body"><b>[[ t.description || t.title || '提醒' ]]</b>
      <small>[[ (t.cron_expr || t.cron || '') + ' · 下次 ' + (t.next_run_at || '') ]]</small>
      <button class="btn ghost" style="margin-top:8px" @click="del(t)">删除</button>
    </div>
  </div>
  <p class="lead" v-if="!tasks.length">还没有提醒。</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({ delimiters:['[[',']]'], data(){return{tasks:[]};},
  methods:{ async load(){ const r=await (await fetch('/app/api/scheduled-tasks')).json(); if(r.ok) this.tasks=r.tasks||[]; },
    async del(t){ const r=await (await fetch('/app/api/scheduled-tasks/'+t.id,{method:'DELETE'})).json(); if(r.ok) this.load(); } },
  mounted(){ this.load(); } }).mount('#app');
</script>
{% endblock %}
```

> 任务对象由 `_task_to_dict()` 生成，含 `id/description/cron_expr/next_run_at/enabled` 等；模板已用 `||` 回退兼容 `cron`。

- [ ] **Step 2: people.html**

```html
{% extends "base_2c.html" %}
{% block page_title %}认识的人 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>认识的人</h1><p>机器人通过摄像头认识的人。</p></div></div>
<div id="app">
  <div class="people">
    <div class="person" v-for="p in profiles" :key="p.person_id">
      <div class="pa" style="background:linear-gradient(135deg,#6c5ce7,#a18bff)">[[ (p.name||'?').slice(0,1) ]]</div>
      <b>[[ p.name || ('#'+p.person_id) ]]</b>
      <button class="btn ghost" style="margin-top:6px" @click="del(p)">删除</button>
    </div>
  </div>
  <p class="lead" v-if="!profiles.length">还没有认识的人。</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({ delimiters:['[[',']]'], data(){return{profiles:[]};},
  methods:{ async load(){ const r=await (await fetch('/app/api/face-profiles')).json(); if(r.ok) this.profiles=r.profiles||[]; },
    async del(p){ const r=await (await fetch('/app/api/face-profiles/'+p.person_id,{method:'DELETE'})).json(); if(r.ok) this.load(); } },
  mounted(){ this.load(); } }).mount('#app');
</script>
{% endblock %}
```

> `person_id` / `name` 字段名以 `GET /app/api/face-profiles` 实际返回为准。

- [ ] **Step 3: devices.html**

```html
{% extends "base_2c.html" %}
{% block page_title %}我的设备 · 小歪{% endblock %}
{% block extra_head %}<script src="{{ url_for('static', filename='vendor/vue.global.prod.min.js') }}"></script>{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>我的设备</h1><p>绑定与切换你的 Brufik 机器人。</p></div></div>
<div id="app">
  <div class="dev" v-for="d in devices" :key="d.device_id">
    <div class="di">🤖</div>
    <div style="flex:1"><b>[[ d.display_name ]]</b><br><small>[[ d.device_id ]]</small></div>
    <button class="btn" :class="d.device_id===current?'primary':'ghost'" @click="select(d)">[[ d.device_id===current?'当前':'切换' ]]</button>
  </div>
  <div style="display:flex;gap:8px;margin-top:14px;max-width:560px">
    <input v-model="newId" placeholder="设备 ID" style="flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:12px">
    <button class="btn primary" @click="bind">绑定</button>
  </div>
  <p class="lead" v-if="msg">[[ msg ]]</p>
</div>
{% endblock %}
{% block scripts %}
<script>
const { createApp } = Vue;
createApp({ delimiters:['[[',']]'], data(){return{devices:[],current:'',newId:'',msg:''};},
  methods:{
    async load(){ const r=await (await fetch('/app/api/devices')).json(); if(r.ok){ this.devices=r.devices||[]; this.current=r.current_device_id||''; } },
    async select(d){ const r=await (await fetch('/app/api/devices/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:d.device_id})})).json(); if(r.ok){ this.current=r.current_device_id; } },
    async bind(){ const id=this.newId.trim(); if(!id) return; const r=await (await fetch('/app/api/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:id})})).json(); if(r.ok){ this.newId=''; this.load(); } else this.msg=r.error||'绑定失败'; } },
  mounted(){ this.load(); } }).mount('#app');
</script>
{% endblock %}
```

> 设备图标 `🤖` 是内容占位，可换成 Lucide `device` 宏（`{{ icon('device') }}` 需在 macro 作用域内；如不便，保留中性图形）。

- [ ] **Step 4: 手动验收**：三页分别能列出提醒/人/设备，删除/绑定/切换生效。

- [ ] **Step 5: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/reminders.html \
        src/deskbot_server/web/templates/app2c/people.html \
        src/deskbot_server/web/templates/app2c/devices.html
git commit -m "feat(web2c): reminders/people/devices pages via existing /app/api/*"
```

---

### Task 9: 高级 hub

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/advanced.html`

- [ ] **Step 1: 写 hub（链接既有页面，不迁移路由）**

```html
{% extends "base_2c.html" %}
{% block page_title %}高级 · 小歪{% endblock %}
{% block content %}
<div class="pagehd"><div><h1>高级</h1><p>开发者与进阶设置，普通使用一般用不到。</p></div></div>
<a class="adv-item" href="/app/usage"><div class="l">用量与账单</div><span>›</span></a>
<a class="adv-item" href="/app/llm-models"><div class="l">LLM 模型</div><span>›</span></a>
<a class="adv-item" href="/app/settings"><div class="l">账号 / API Key</div><span>›</span></a>
<a class="adv-item" href="/debug/devices"><div class="l">调试 · 设备</div><span>›</span></a>
<a class="adv-item" href="/debug/tts"><div class="l">调试 · TTS</div><span>›</span></a>
<a class="adv-item" href="/debug/llm"><div class="l">调试 · LLM</div><span>›</span></a>
<a class="adv-item" href="/debug/simulation"><div class="l">调试 · 模拟</div><span>›</span></a>
<p class="lead" style="margin-top:16px">＊ 这些都是既有控制台页面，功能不变，只是默认收起。</p>
{% endblock %}
```

在 `theme_2c.css` 末尾补 `a.adv-item{text-decoration:none}`（原型 `.adv-item` 是 div）。

- [ ] **Step 2: 手动验收**：`/advanced` 列出链接，点击进入既有页面且功能正常。

- [ ] **Step 3: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/advanced.html src/deskbot_server/web/static/theme_2c.css
git commit -m "feat(web2c): advanced hub linking existing admin/debug pages"
```

---

### Task 10: 回归 — 既有页面与测试不受影响

- [ ] **Step 1: 跑全量测试**

Run: `cd service/deskbot-server && python -m pytest -q`
Expected: 全部 PASS（既有用例 + 新增 `test_app2c_pages.py`）

- [ ] **Step 2: 手动回归**：旧入口 `/app`、`/debug/*` 仍可访问、行为不变。

- [ ] **Step 3: Commit（如有微调）**

```bash
git add -A && git commit -m "test(web2c): full suite green after 2C shell"
```

---

## Phase 2 — 情绪→场景映射（可选）

> 仅当确认需要"给情绪指定表情场景"的持久化时实施。这是本计划唯一的新增后端。

### Task 11: emotion_expr_map_store（TDD）

**Files:**
- Create: `src/deskbot_server/emotion_expr_map_store.py`
- Create: `src/deskbot_server/constants.py` 中新增常量 `EMOTION_EXPR_MAP_FILE`（若 `constants.py` 已集中管理文件名）
- Test: `tests/test_emotion_expr_map_store.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_emotion_expr_map_store.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def map_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "emotion_expr_map.json"
        monkeypatch.setattr(
            "deskbot_server.emotion_expr_map_store.EMOTION_EXPR_MAP_FILE",
            str(path),
        )
        yield path


def test_load_defaults_empty(map_file):
    from deskbot_server.emotion_expr_map_store import load_emotion_expr_map

    assert load_emotion_expr_map(device_id=None) == {}


def test_save_then_load_roundtrip(map_file):
    from deskbot_server.emotion_expr_map_store import (
        load_emotion_expr_map,
        save_emotion_expr_map,
    )

    save_emotion_expr_map({"happy": "smile", "sad": "sad"}, device_id=None)
    assert load_emotion_expr_map(device_id=None) == {"happy": "smile", "sad": "sad"}


def test_save_rejects_non_string_values(map_file):
    from deskbot_server.emotion_expr_map_store import save_emotion_expr_map

    with pytest.raises(ValueError):
        save_emotion_expr_map({"happy": 123}, device_id=None)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd service/deskbot-server && python -m pytest tests/test_emotion_expr_map_store.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 store**

```python
# src/deskbot_server/emotion_expr_map_store.py
from __future__ import annotations

import json
import os
from typing import Optional

from deskbot_server.device_data import resolve_json_path

EMOTION_EXPR_MAP_FILE = "emotion_expr_map.json"


def _normalize(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("emotion map must be an object")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(v, str):
            raise ValueError(f"scene for emotion {k!r} must be a string")
        out[str(k)] = v
    return out


def load_emotion_expr_map(*, device_id: Optional[str] = None) -> dict[str, str]:
    path = resolve_json_path(EMOTION_EXPR_MAP_FILE, device_id)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return _normalize(json.load(f))


def save_emotion_expr_map(
    mapping: dict[str, str], *, device_id: Optional[str] = None
) -> dict[str, str]:
    norm = _normalize(mapping)
    path = resolve_json_path(EMOTION_EXPR_MAP_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return norm
```

- [ ] **Step 4: 运行确认通过**

Run: `cd service/deskbot-server && python -m pytest tests/test_emotion_expr_map_store.py -q`
Expected: PASS（3 用例）

- [ ] **Step 5: Commit**

```bash
git add src/deskbot_server/emotion_expr_map_store.py tests/test_emotion_expr_map_store.py
git commit -m "feat(expr-map): per-device emotion->scene map store with tests"
```

---

### Task 12: 映射接口 GET/POST /api/emotion_expr_map（TDD）

**Files:**
- Modify: `src/deskbot_server/web/blueprints/app2c_bp.py`
- Test: `tests/test_emotion_expr_map_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_emotion_expr_map_api.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DESKBOT_DB_PATH", str(Path(tmp) / "t.db"))
        from deskbot_server.db import init_database
        from deskbot_server.db.engine import init_engine, reset_engine

        reset_engine()
        init_engine(Path(tmp) / "t.db")
        init_database()
        yield


def _client():
    from deskbot_server.auth.device_service import bind_device
    from deskbot_server.auth.service import create_user
    from deskbot_server.web.app import create_app

    user = create_user("map@example.com", "password1234")
    bind_device(user.id, "deskbot_map")
    app = create_app()
    c = app.test_client()
    c.post("/login", data={"email": "map@example.com", "password": "password1234"})
    c.post("/app/api/devices/select", json={"device_id": "deskbot_map"})
    return c


def test_get_empty_then_post_roundtrip(temp_db):
    c = _client()
    g = c.get("/api/emotion_expr_map").get_json()
    assert g["ok"] is True and g["map"] == {}

    p = c.post("/api/emotion_expr_map", json={"map": {"happy": "smile"}}).get_json()
    assert p["ok"] is True

    g2 = c.get("/api/emotion_expr_map").get_json()
    assert g2["map"] == {"happy": "smile"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd service/deskbot-server && python -m pytest tests/test_emotion_expr_map_api.py -q`
Expected: FAIL（路由不存在 → 404）

- [ ] **Step 3: 实现接口**（追加到 `app2c_bp.py`）

```python
from flask import jsonify, request
from flask_login import current_user

from deskbot_server.auth.device_service import user_owns_device
from deskbot_server.emotion_expr_map_store import (
    load_emotion_expr_map,
    save_emotion_expr_map,
)
from deskbot_server.web.session_device import get_current_device_id


def _owned_device_or_error():
    device_id = (request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not user_owns_device(current_user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


@bp.get("/api/emotion_expr_map")
@login_required
def emotion_expr_map_get():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    return jsonify({"ok": True, "device_id": device_id, "map": load_emotion_expr_map(device_id=device_id)})


@bp.post("/api/emotion_expr_map")
@login_required
def emotion_expr_map_post():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    mapping = payload.get("map")
    if not isinstance(mapping, dict):
        return jsonify({"ok": False, "error": "map 必须是对象"}), 400
    try:
        saved = save_emotion_expr_map(mapping, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "map": saved})
```

> 说明：`device_id` 来自 query 或 session 当前设备；POST 用 JSON body（无 query 时回退 session）。POST 路径下 `request.args.get('device_id')` 为空将回退 `get_current_device_id()`，测试已 `select` 当前设备。

- [ ] **Step 4: 运行确认通过**

Run: `cd service/deskbot-server && python -m pytest tests/test_emotion_expr_map_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deskbot_server/web/blueprints/app2c_bp.py tests/test_emotion_expr_map_api.py
git commit -m "feat(expr-map): GET/POST /api/emotion_expr_map endpoints with tests"
```

---

### Task 13: 表情页接入情绪映射

**Files:**
- Modify: `src/deskbot_server/web/templates/app2c/expr.html`

- [ ] **Step 1: 在表情页加情绪→场景下拉**

在 Task 6 的 `expr.html` 内容区追加一段「情绪 → 表情」映射区（参考原型 `#emoMap` 与 `.maprow`），并在 Vue 中：
- `data` 增加 `emotions:[['happy','开心'],['surprised','惊讶'],['thinking','思考'],['sleepy','困'],['idle','待机']]` 与 `map:{}`。
- `mounted`/`load()` 末尾 `this.map = (await (await fetch('/api/emotion_expr_map')).json()).map || {}`。
- 每行一个 `<select v-model="map[key]">`，选项为 `scenes` 的 `name`（显示 `title`）。
- `save()` 末尾追加：`await fetch('/api/emotion_expr_map',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({map:this.map})})`。

完整片段：

```html
<div class="blocktitle">情绪 → 表情</div>
<div class="maprow" v-for="[k,label] in emotions" :key="k">
  <div class="lab">[[ label ]]</div>
  <select v-model="map[k]">
    <option value="">（不指定）</option>
    <option v-for="s in scenes" :key="s.name" :value="s.name">[[ s.title || s.name ]]</option>
  </select>
</div>
```

```javascript
// data(): 追加
emotions:[['happy','开心'],['surprised','惊讶'],['thinking','思考'],['sleepy','困'],['idle','待机']],
map:{},
// load() 末尾追加
this.map = ((await (await fetch('/api/emotion_expr_map'+(this.deviceId?('?device_id='+encodeURIComponent(this.deviceId)):''))).json()).map) || {};
// save() 末尾追加（在原 face_expr_scenes 保存之后）
await fetch('/api/emotion_expr_map',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({device_id:this.deviceId, map:this.map})});
```

- [ ] **Step 2: 手动验收**：表情页可给情绪选场景、保存后刷新仍在；`GET /api/emotion_expr_map` 复核。

- [ ] **Step 3: 跑全量测试**

Run: `cd service/deskbot-server && python -m pytest -q`
Expected: 全绿

- [ ] **Step 4: Commit**

```bash
git add src/deskbot_server/web/templates/app2c/expr.html
git commit -m "feat(web2c): wire emotion->scene mapping into expression page"
```

---

## 落地后说明（运行）

启动（既有方式）：`cd service && ./start.sh`，或单独 web：`cd service/deskbot-server && python -m deskbot_server.web`。
登录 `http://<IP>:5050/login` 后自动落地 `/home`。开发者入口仍在 `/app`、`/debug/*`，并由 `/advanced` 聚合。

## 接口形状（均已实测确认）
- `GET /api/face_expr_scenes` → 场景在 `config` 键下；POST body `{device_id, scenes:[…]}` 兼容。
- `GET /app/api/scheduled-tasks` → `{ok, tasks:[{id, description, cron_expr, next_run_at, enabled, …}]}`。
- `GET /app/api/face-profiles` → `{ok, profiles:[{person_id, name, …}]}`。
- `GET /api/doubao_tts/speakers` / `config` / `synthesize`、`/app/api/memories|devices` 均已在上文任务按真实返回接线。
