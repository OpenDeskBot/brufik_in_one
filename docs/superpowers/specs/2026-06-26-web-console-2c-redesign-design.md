# Web 控制台 2C 化改造 · 设计文档

- 日期：2026-06-26
- 范围：`service/deskbot-server` 的 Web 控制台（Flask + Jinja + 内联 Vue）
- 性质：交互层（前端 + 薄接口）重构，**不改动核心管线、协议、固件、调度、识别能力**

## 1. 背景与目标

现有 Web 控制台（端口 5050）是面向开发者/管理员的形态：侧边栏平铺 8+ 个功能页（dashboard / devices / scheduled-tasks / face-profiles / memories / llm-models / usage / account），外加 6 个 debug 调试页。术语和信息密度都偏工程向。

目标：把它改造成一个**面向普通用户（2C）的配套 App**——机器人本体仍是 ESP32 实体设备，网页是它的消费级配套（不另做原生客户端）。要求：

1. 所有复杂配置默认隐藏。
2. 把 **表情定制** 和 **声音定制** 做成两个核心可玩功能（产品主角）。
3. 复用现有后端能力，不改核心功能逻辑。

## 2. 非目标（明确不做）

- 不改动 ASR / LLM / TTS 管线、pb 协议、设备固件、定时调度、人脸识别等核心逻辑。
- 不做 **语速 / 音调** 调节（豆包 TTS 当前仅 `speaker` + `sample_rate`，无 speed/pitch）。
- 不做 **声音克隆**（后端无克隆能力）。
- 不删除任何现有功能；用量/账单、API Key、LLM 模型、debug 页全部保留，仅降权隐藏。
- 不引入新的前端框架（继续 Flask + Jinja + 内联 Vue）。

## 3. 现有能力盘点（设计的事实依据）

### 已有完整 JSON 接口（纯前端即可重新包装）
- 设备：`GET/POST /app/api/devices`、`POST /app/api/devices/select`、`DELETE /app/api/devices/<id>`
- 记忆：`GET/POST /app/api/memories`、`GET/PUT/PATCH/DELETE /app/api/memories/<id>`
- 提醒（定时任务）：`GET /app/api/scheduled-tasks`、`DELETE /app/api/scheduled-tasks/<id>`
- 人脸：`GET /app/api/face-profiles`、`DELETE /app/api/face-profiles/<person_id>`
- 用量：`/app/usage`（页面级）

### 已有 Web JSON 接口（实测，可直接被前端复用 —— 修正：并非"缺接口"）
两个英雄功能的后端接口其实**已存在于 `debug_bp`**，无需新增：
- 声音：
  - `GET /api/doubao_tts/speakers` → 音色画廊（`{ok, speakers:[{label,id,scene,resource_id,...}]}`）。
  - `GET /api/doubao_tts/config` / `POST /api/doubao_tts/config` → 读/设当前音色（POST 经 `save_doubao_tts_env` 写 `DOUBAO_TTS_SPEAKER` 并刷新进程环境，**全局生效**）。
  - `POST /api/doubao_tts/synthesize` → 试听，返回 `wav_base64`（浏览器可直接播放）。
- 表情：
  - `GET /api/face_expr_scenes?device_id=` → 按设备读场景（`load_face_expr_scenes_file`，缺省自动 seed `builtin_emotion_scenes()`）。
  - `POST /api/face_expr_scenes` → 按设备写场景（`save_face_expr_scenes_file`）。
- 场景数据形状：`{name:[a-z0-9_], title, frames:[{ms, elements:{mouth,nose,eye_l,eye_r,extra}}]}`。
- 音量：已支持（LLM JSON `volume` 字段 + 设备播放音量）。

> 影响：原计划的"4 个新薄接口"**作废**。改造主体变为**纯前端**（消费级外壳 + 页面，调用上述既有接口）。
> 唯一可能新增的后端 = 一个极小的 **per-device 情绪→场景映射 store**（见 §7.2 / §5.1，列为可选 Phase 2）。

## 4. 信息架构（三层）

> 形态：**桌面 Web App**（非移动端）。采用左侧固定导航栏 + 右侧内容区的经典 Web 布局；
> 一级导航项 = 家 / 表情 / 声音 / 它记得的事 / 提醒 / 认识的人 / 我的设备，底部一个降权的「⚙️ 高级」。

### ① 首页「家」（Home）
进入即见机器人：
- 大表情预览区（复用设备 284×240 画面渲染）。
- 状态条：在线/离线、当前音色名、当前性格/表情、"记得 N 件事 / N 个提醒"。
- 两张 C 位大卡片入口：🎭 捏表情 · 🔊 调声音。

### ② 中层功能
- 英雄功能（独立全屏体验）：🎭 表情、🔊 声音。
- 生活化功能（卡片入口，二级）：🧠 它记得的事、⏰ 提醒、👤 认识的人、📱 我的设备。

### ③ 底层「⚙️ 高级」
置于页脚或头像菜单，默认折叠、视觉降权。把现有管理页 **原样搬入** `/advanced/*`：用量/账单、API Key、LLM 模型、所有 debug 调试页。功能不删，普通用户不可见。

> 核心手法：不删功能，只**重新分层 + 重新命名 + 重新叙事**。
> face-profiles → 认识的人；memories → 它记得的事；scheduled-tasks → 提醒。

## 5. 英雄功能详细设计

### 5.1 🎭 表情定制
背靠 `face_expr_scenes_store`（per-device）。

- **皮肤打底**：顶部一排预设"面孔风格"缩略图（来自 `builtin_emotion_scenes()` / 现有场景），点击整套切换，右侧大图实时预览。
- **情绪挂表情**：情绪清单（开心 / 惊讶 / 思考 / 困 / 待机…），每个情绪一个下拉，映射到某个表情场景（即"情绪-表情映射"）。
- **所见即所得**：选中即在 284×240 大脸上播放该场景动画（纯前端用现有动画数据渲染）。
- **保存**：调薄接口写回 per-device 表情/映射数据。

### 5.2 🔊 声音定制
背靠 `doubao_tts_speakers.json` + `tts/speakers.py`。

- **音色画廊**：卡片网格列出豆包音色，按 `scene` 分组（客服 / 教育 / 有声阅读 / 视频配音…）。
- **试听**：每张卡 ▶️ 试听，走现有 TTS 合成路径念一句固定示例（不新增合成能力）。
- **设为当前音色**：写当前 speaker。
- **音量**：滑杆（后端已支持）。
- 不含语速/音调/克隆。

## 6. 生活化功能（纯前端，零后端改动）

| 现有 | 改名 | 交互 | 背靠接口 |
|---|---|---|---|
| memories | 🧠 它记得的事 | 便利贴式记忆卡，增/改/删 | `/app/api/memories`（已全有） |
| scheduled-tasks | ⏰ 提醒 | 时间轴列表，可删 | `/app/api/scheduled-tasks`（已有列/删） |
| face-profiles | 👤 认识的人 | 头像网格 + 名字，改名/删 | `/app/api/face-profiles`（已有列/删） |
| devices | 📱 我的设备 | 设备卡 + 在线灯 + 绑定向导 | `/app/api/devices`（已全有） |

dashboard 保留但瘦身，并入首页"家"页的状态条。

## 7. 技术落地

### 7.1 前端
- 继续 Flask + Jinja + 内联 Vue + `theme.css`，不引新框架。
- 新增消费级布局母版 `base_2c.html` 与新主题 `theme_2c.css`。
- 现有 `base.html` + 全部管理/调试页**原封不动**搬到 `/advanced/*` 作为"高级"区。
- 图标统一用 **Lucide**（ISC ≈ MIT 许可，24px 栅格 / 2px 圆角描边）；不使用 emoji 作为导航或卡片标签。可经包管理器引入或内联 SVG，保持全站一套图标语言。

### 7.2 后端改动（修正后：Phase 1 零新增，Phase 2 仅一个极小 store）

**Phase 1 — 零新增后端**：声音/表情/记忆/提醒/人/设备全部调用既有 JSON 接口（见 §3）。
唯一后端层面的改动是把**登录后落地页**指向新的消费首页（一处 redirect）。

**Phase 2（可选）— 情绪→场景映射**：现有 `face_expr_scenes_store` 存的是"场景列表"，不含"情绪→场景"映射。
若要实现 §5.1 的情绪映射持久化，新增一个极小 per-device store：

| 新增 | 作用 | 复用机制 |
|---|---|---|
| `emotion_expr_map_store.py` | 读/写 per-device `emotion_expr_map.json`（`{emotion_key: scene_name}`） | `device_data.resolve_json_path`（与 `face_expr_scenes_store` 同一 per-device 机制） |
| `GET/POST /api/emotion_expr_map` | 前端读写映射 | 上述 store |

- 试听复用既有 `POST /api/doubao_tts/synthesize`，不新增能力。
- 新增接口沿用现有 `before_request` 鉴权与 per-device 约定。

### 7.3 明确不动
ASR / LLM / TTS 管线、pb 协议、设备固件、定时调度、人脸识别。

## 8. 验收标准

1. 普通用户登录后首屏即见机器人 + 表情/声音两个入口，看不到任何 debug/API Key/模型/用量术语。
2. 表情：能切换皮肤、给至少 4 种情绪指定表情场景、实时预览、保存后通过 `GET /app/api/expressions` 可读回。
3. 声音：能浏览按场景分组的音色画廊、试听、设为当前音色（`POST /app/api/voice/active` 生效）、调音量。
4. 记忆/提醒/认识的人/设备四项均以新叙事呈现，且 CRUD 行为与原接口一致。
5. `/advanced/*` 下原有全部管理/调试功能可访问、行为不变。
6. 核心管线/协议/固件无任何代码改动。

## 9. 风险与依赖

- 表情实时预览需在浏览器用现有动画数据渲染 284×240 画面；若现有 pb/动画数据格式不便于前端直接渲染，预览可降级为"静态首帧 + 场景名"，不阻塞保存能力。
- 试听依赖 TTS 服务在线；离线时按钮置灰并提示。
- 设当前音色的写入位置（env vs config.yaml）需在实现期确认生效路径，确保与设备实际取用一致。
