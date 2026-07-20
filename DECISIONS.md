# 决策记录

产品定位讨论、市场部沟通记录、方向权衡等决策历史。
claude.md 中仅留索引，避免每次代码会话加载无关上下文。

---

## 当前推进节奏说明（2026-07）

- **决策**：当前阶段优先自行完成技术验证（修复已知 bug + 图片方案 + PDF
  支持），做出一版可演示效果后，再去找市场部收集真实使用习惯反馈、并索
  取更多历史方案材料，而非反过来先做需求调研再动手。这是当前阶段的有意
  选择。需要注意：涉及数据结构的决定（如图片索引方式、跟随式图片设计）
  应保持轻量化实现，以便后续根据市场部反馈调整，避免过度设计。

## 测算模块（光储充）价值边界的判断标准（2026-07）

- Excel-in/Excel-out 若仅为"读输入区→跑原公式→写输出区"，与市场部
  现有 excel 无实质差异，不构成产品增量。
- 增量价值只能来自以下之一，需明确选择：
  (a) 从 RAG 检索的历史项目自动预填参数（省去人工誊抄）
  (b) 测算结果自动写回生成的大纲对应位置（省去人工复制粘贴）
- 若两者都不做，建议此模块暂不投入，市场部继续用现有 excel。
- 若要做，优先验证 (a)，实现和验证成本低于 (b)。

## MinerU 稳定性问题排查结论（2026-07）

- **现象**：本周 MinerU 云端 API 出现连续卡顿/调用失败，一度怀疑是本地
  环境配置或方案本身设计有问题。
- **结论**：周六（07-11）起恢复正常，排查确认是官网服务端本身的临时性
  不稳定，并非本地环境、调用方式或本方案设计的问题。
- **决策：不更换解析方案**。理由：MinerU 免费、速度快、当前解析质量能
  满足需求，切换到其他方案（重新适配 adapter、重新验证效果）成本高、
  收益不明确，没有必要。继续按现有方式使用，后续如再次出现长时间
  （非偶发）不稳定，再评估是否需要备选方案。
- **已知强约束（需长期记住）**：本机不能挂任何代理（VPN/proxy）——一旦
  开启代理，MinerU 的上传/调用无法正常完成。日常调用 `ingest` 前需确认
  代理已关闭，排查"解析失败"问题时应第一时间检查代理状态，避免误判为
  官网故障或代码 bug。

## 常用命令记录（2026-07）

- **入库**（解析并写入向量库）：
  ```
  python src/main.py ingest "files/康晋电气工商业光储充一体化解决方案.pptx"
  ```
- **查询 / 生成**（检索历史素材并生成方案初稿）：
  ```
  uv run python src/main.py query "光伏储能一体化方案"
  ```
- 注：两条命令目前分别按各自实际验证通过的方式记录（一个直接 `python`，
  一个 `uv run python`），暂不强行统一写法，避免记错导致命令跑不通。

---

## 开发路线图

### 阶段一：入库（已完成）
- [x] parser.py — MinerU 云 API 封装（两步上传）
- [x] 用一份真实 PPTX 跑通 parser，确认按 page_idx 分组比 Markdown 分隔符更稳定
- [x] vector_store.py — Chroma 封装（按 slide 存入，支持语义/关键词检索）
- [x] main.py — 串联 parser → vector_store，CLI 入口：ingest / query / status
- [x] 验证：入库后能按关键词检索到正确 slide
- [x] 图片提取：MinerU 返回的图片文件提取到本地 images/ 目录，关联到对应 slide
- [x] 版本控制：文件哈希判重，内容未变跳过入库，变了才清空重建

### 阶段二：生成（基本完成，部分 bug 已修）
- [x] llm_client.py — LLM API 封装（DashScope Qwen，qwen3.7-max，兼容 OpenAI 接口）
- [x] 检索策略设计：query 改写（1-3 个）→ metadata 过滤 → 向量检索 → 相邻 slide 扩展
- [x] 检索 + 生成：`query "需求描述"` 已跑通整条链路（改写→检索→相邻扩展→生成初稿）
- [x] Prompt 设计：`_GENERATION_SYSTEM_PROMPT` + `_QUERY_REWRITE_SYSTEM_PROMPT` + `_CONTEXT_METADATA_SYSTEM_PROMPT` 均已上线
- [x] 引用溯源防幻觉：prompt 中注入可引用编号白名单 + 生成后正则校验（2026-07 修复）
- [ ] 输出为 Markdown 文件：目前生成结果只打印到终端，未持久化为 .md 文件
- [ ] 已知 bug 待修：方案类型过滤失效（#3）— 见下方"已知问题详情"章节

### 阶段三：工程化（交付就绪）
- [ ] pyproject.toml — uv 管理依赖，别人能一键 uv sync
- [ ] .env.example — 列出所有需要配的环境变量
- [ ] .gitignore — 排除 files/、.env、__pycache__、chroma_db/
- [ ] README.md — 三步跑起来：装依赖、配环境变量、运行 main.py
- [ ] docs/data_schemas.md — 记录各层之间的数据格式（SlideDict、Chroma document 结构）

### 阶段四：demo 包装（给公司看）
- [x] 简单 CLI 入口：python main.py ingest / python main.py query "xxx" / python main.py status
- [ ] 或者极简 Web UI（Gradio 一个文件搞定，不需要前后端分离）
- [ ] 一页 PPT 说明系统能做什么、用了什么技术、效果对比

### 后续可扩展（确认 demo 有价值后再做）
- [ ] 支持批量入库多份 PPTX
- [ ] 图片 caption 质量优化：抽查 MinerU 生成的 caption，质量不够的话考虑用视觉模型重新生成描述
- [ ] 方案模板结构化输出（不只是 Markdown，而是有章节的文档）
- [ ] 接入公司内网部署

---

## 当前项目状态（2026-07 基于实际代码梳理）

### CLI 命令及完成度

| 命令 | 入口 | 状态 | 说明 |
|------|------|------|------|
| `ingest <pptx>` | `pipeline.py:ingest()` | **完整** | 文件哈希判重 → MinerU 解析 → 向量库裸存 → 逐张 LLM 生成 context → 批量写回 |
| `annotate <source_file>` | `pipeline.py:annotate()` | **完整** | 从 Chroma 已入库内容补跑 context 增强，不重新调用 MinerU |
| `query "需求描述"` | `pipeline.py:query()` | **可用但有已知 bug** | query 改写 → metadata 过滤 → 向量检索 → 相邻扩展 → 生成初稿；存在第 2 条引用幻觉、第 3 条过滤失效问题 |
| `status` | `pipeline.py:status()` | **完整** | 显示 slide 总数 + 已入库文件列表 |

### 模块实现状态

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| MinerU 解析 | `src/adapters/parser.py` | **完整（仅 PPTX）** | `parse_pptx()` 两步上传+轮询，按 page_idx 分组，图片提取到 `images/`，debug zip 保存；无 PDF/DOCX 通道 |
| 向量库 | `src/adapters/vector_store.py` | **完整** | Chroma + DashScope embedding（分批+重试）；add/search/adjacent/delete/context update 全部实现；images 序列化存 metadata |
| LLM 生成 | `src/adapters/llm_client.py` | **完整** | DashScope Qwen（qwen3.7-max），generate/rewrite_query/generate_slide_context 均实现，429/5xx 自动重试 |
| 业务编排 | `src/pipeline.py` | **可用** | ingest/query/annotate/status 全链路串通；但存在已知 bug（见下） |
| CLI 入口 | `src/main.py` | **完整** | 4 个命令分发到 pipeline |
| 测试 | `src/test.py` | **草稿** | 仅手动验证 MinerU SDK 的临时脚本，不是自动化测试 |

### 工程化清单（对比路线图）

| 项目 | 状态 | 备注 |
|------|------|------|
| `pyproject.toml` | 已创建 | 声明 `mineru-open-sdk>=0.1`（PyPI 包名），实际 `from mineru import MinerU`（import 名），两者一致 |
| `.env.example` | 已创建 | 列出 MINERU_TOKEN + DASHSCOPE_API_KEY |
| `.gitignore` | 部分完成 | 含 .env/chroma_db/__pycache__/.venv，但 roadmap 要求的 `files/` 未列入 |
| `README.md` | **未创建** | 三步跑起来的文档缺失 |
| `docs/data_schemas.md` | **未创建** | SlideDict/Chroma document 结构未文档化 |
| Web UI (Gradio) | **未创建** | 阶段四待办 |

### 待确认细节（开发时发现）

- `parser.py:14` import 的是 `from mineru import MinerU`，而 `pyproject.toml` 依赖写的是 `mineru-open-sdk>=0.1`，需确认实际安装的是哪个包，两者是否一致
- `test.py` 是临时调试脚本（硬编码 `"files/测试测试.pptx"`），不属于正式测试套件，建议清理或转为正式测试

---

## 已知问题详情

### 1. 表格解析逐字符竖排问题 ~~（已修复，2026-07-20）~~
- **现象**：已入库的部分表格在 Chroma 中表现为逐字符竖排的乱码格式，
  如每个字符独立成一行（`<`, `t`, `a`, `b`, `l`, `e`, `>`, ...），
  导致语义检索时表格内容无法被正确命中。
- **涉及代码**：`parser.py:257-264`（`_render_page` 中 table_body 处理逻辑）
- **根因**：MinerU 返回的 `table_body` 并非按"行 → 单元格"组织的二维列表，
  而是一个逐字符的字符串列表（如 `['<', 't', 'a', 'b', ...]`），把它们
  join 后得到完整的 HTML 表格标记（`<table><tr><td>...</td></tr>...</table>`）。
  旧代码直接 `for row in table_body` 遍历，把每个单字符当作一行处理，
  导致每个字符被独立 append 进 parts，最终存进 Chroma 的文本变成竖排。
- **修复方式**（2026-07-20）：
  1. 新增 `_TableTextParser(HTMLParser)` 类（标准库），用于解析 join 后的
     HTML 表格标记，按 `<tr>` 分组、逐行提取 `<td>` 文本内容。
  2. 单元格内容经 `html.unescape()` 解码 HTML 实体（如 `&amp;` → `&`）。
  3. `colspan` 属性忽略，按 `<td>` 出现顺序输出，每行用 ` | ` 分隔。
     理由：RAG 语义检索只关心文本内容正确性和行结构，不关心列对齐。
  4. `table_caption` 兼容 list 和 string 两种类型（MinerU 有时返回 `[]`）。
- **影响范围**：仅影响修复后新解析并入库的文件。已入库的历史文件中表格
  内容仍为旧的逐字符格式，需要重新 ingest 才能修复（见下方提醒）。

> **⚠️ 需重新 ingest 的文件提醒**：以下已入库文件的表格内容受此 bug 影响，
> 如需表格内容参与语义检索，应对这些文件重新调用 `ingest`：
> - `files/中南表面处理产业园智慧园区解决方案.pptx`（4 个表格）
> - `files/康晋电气工商业光储充一体化解决方案.pptx`（5 个表格）
> - 以及其他所有在此修复之前入库的含表格文件。
>
> 重新 ingest 前请先确认源 pptx 文件仍存在于 `files/` 目录下，
> 版本控制会自动跳过哈希未变的文件。

### 2. 引用溯源存在幻觉（citation hallucination）
- **现象**：一次实测查询（"光伏储能一体化方案"）中，最终生成稿引用了
  Slide 3、Slide 4、Slide 9，但这三个编号从未出现在该次检索日志的主结果
  或相邻扩展结果中（实际检索到并送入生成上下文的 slide 集合为
  1,2,5,6,7,8,10-21）。说明生成模型在编造引用编号，而非从真实检索到的
  内容中选择。
- **涉及代码**：`pipeline.py:_GENERATION_SYSTEM_PROMPT`
- **状态**：**已修（2026-07）**：prompt 已注入可引用编号白名单 + 生成后正则校验
- **原始待办**（已完成）：
  - 在生成 prompt 中明确列出本次上下文里实际可引用的 slide 编号集合
  - 生成后增加校验：正则提取输出中的所有 "Slide N" 编号，与实际传入
    context 的编号集合做差集比对

### 3. 方案类型过滤实际从未生效
- **现象**：query 改写阶段（`llm_client.py:rewrite_query`）推测的方案类
  型为"光储"，但库中实际存储的 `proposal_type` 字段值为"光储充" /
  "光储充一体化"。`vector_store.py:_build_where` 中过滤使用精确字符串匹配
  （`{"proposal_type": proposal_type}`），从未命中，导致每次都触发 fallback
  （不限类型重新检索，`pipeline.py:230-239`）。
- **涉及代码**：`vector_store.py:409` + `pipeline.py:230-239`
- **待办**：改为包含匹配（判断推测类型是否为存储类型的子串，或反之），或
  建立类型同义词归一化表（如"光储"→"光储充"）。
- **优先级**：中。

### 4. 图片描述方案：降低纯手工标注成本
- **方向**：
  - 让 LLM 生成图片描述时，同时提供该图片所在 slide 的文字上下文，使
    描述带有语用信息；
  - 建立图片角色分类体系（如：项目实景图 / 系统架构图 / 产品实物图 /
    数据图表 / 收益测算表），让模型做分类而非自由文本描述。
- **涉及代码**：`parser.py:262-275`（`image_caption` 直取 MinerU 原始值）
- **当前状态**：图片 caption 直接取 MinerU 返回的 `image_caption`，无语境生成。

### 5. Slide 内容存储粒度与生成简繁应分离；规划"引用可点击溯源"功能
- **原则**：存储/检索层的 slide 内容应保留完整原始细节（具体数字、案例名称、
  参数），不应过度精简；生成层的叙述性文字可以保持相对精炼。
- **规划功能**：引用编号（如 "Slide N"）应可点击或悬停跳转，展示该
  slide 的原始文字内容及关联图片（做成信息卡片，不必还原 PPT 视觉样式）。
  该功能强依赖第 2 条（引用溯源幻觉）先修复，否则跳转目标不可信。
- **当前状态**：`vector_store.py:add_slides` 中 title + content 全文存入 document，
  符合"保留完整原始细节"原则。

### 6. 图片入库与溯源关联方式
- **方案**：图片作为其所属 slide 记录的附属字段存储（图片路径 + 角色标
  签 + 上下文关联描述），检索到该 slide 时图片跟随一起返回，而不是为图
  片单独建立可独立检索的向量索引。
- **当前状态**：`images` 字段已序列化为 JSON 字符串存入 slide metadata
  （`vector_store.py:185`），检索时通过 `_slide_from_meta` 还原（line 429-433），
  与该方案方向一致。独立图片检索暂不做。

### 7. 新增 PDF 解析支持
- **背景**：MinerU 目前已原生支持 PDF / DOCX / PPTX / XLSX 解析
  （3.1.0 版本起），PDF 是其最早、最成熟的解析场景。理论上可以复用现有
  adapter 架构（`parser.py`）新增 PDF 输入通道，预期工作量小于当初适配
  PPTX 的成本。
- **备注**：近期使用的 MinerU 官网云端 API 出现连续故障，需先确认是账号 /
  网络问题还是服务方问题，再排期这项开发。

### 8. 当前推进节奏说明
- 决策详情见上方"当前推进节奏说明"章节。

---

## 架构决策：大纲中间表示与导出解耦（v1）

- 生成模块的输出应为格式无关的结构化对象（IR），不掺任何pptx/docx专用代码：
  `[{level, heading, bullets, citations: [{source_file, slide_index}]}, ...]`
- word/pptx 各自对应一个薄导出函数：export_pptx(IR) / export_docx(IR)，
  仅做骨架渲染（标题+要点/文本框），不追求还原历史排版。
- v1阶段无需任何位置/坐标信息，因为不追求排版还原，此问题延后到v3处理。

## v3 换皮方案的技术路径（排版问题的解法）

- 不通过MinerU解析结果重建位置信息（MinerU解析产物无坐标数据，且用途
  是入库检索而非还原排版）。
- 正确路径：利用已有的"检索结果→源文件路径+slide序号"映射（已有，
  用于citation溯源），直接用python-pptx/lxml复制源pptx中对应slide的
  XML对象到新presentation，位置/配色/形状随XML原样保留。
- 复制后仅按角色（标题/正文/图片占位符）定位文本框和图片框做替换，
  不做坐标计算。
- 前提：需确认当前是否保留了原始pptx源文件（而非只存了MinerU解析
  后的文本/图片），若未保留需补上这一存储环节。
  待重新评估事项：生成阶段的选图逻辑（v3 模板渲染阶段）
当前决策：检索/生成阶段（query 命令）固定用纯文本模型（qwen3.7-max），
因为该阶段任务是"读取检索到的文字内容（含图片 caption）生成方案初稿"，
属于纯文本生成任务，不需要视觉能力。
需要重新审视的场景：如果 v3 阶段要做到"从多张候选图片里，判断哪张最适合
插入当前这段文字"，这个任务本质上要求模型"看着图"做筛选/排序，而不是
只读 caption 文字描述，届时纯文本模型可能不够用。
暂定处理方式：v1/v2 阶段不需要处理这个问题，先不做任何改动。等做到 v3
模板渲染、真正涉及"往文档里插具体某一张图"的需求时，再回来决定：
（a）是否需要引入视觉模型做选图，或者
（b）caption 文字质量足够好时，靠文字匹配/相似度也能达到同等效果，不必上视觉模型。
两种方案孰优孰劣，届时应基于实际 caption 质量和插图场景的复杂度重新判断。





## MinerU Cloud API 参考（开发时查阅）

**认证**
- 请求头：`Authorization: Bearer {token}`
- Token 从环境变量 `MINERU_TOKEN` 读取
**调用模式**
- 异步两步：提交任务 → 轮询结果
- 本地文件流程：POST `/api/v4/file-urls/batch` 拿预签名上传 URL → PUT 上传文件 → 轮询 GET `/api/v4/extract-results/batch/{batch_id}`
- 无公网服务器，不使用 callback，用轮询
**模型版本**
- PPTX 使用 `vlm`（精度最好，支持图文混排）
- HTML 文件才用 `MinerU-HTML`
- `pipeline` 是默认值但精度较低，不用
**输出格式**
- 使用 Markdown（结构清晰，适合向量库存储和 slide 切分）
**文件限制**
- 单文件最大 200MB，最多 200 页
- 支持格式：PDF、PNG/JPG/JPEG/JP2/WEBP/GIF/BMP、DOC/DOCX、PPT/PPTX、XLS/XLSX
**额度**
- 每账号每天 2000 页最高优先级，超出后优先级降低

已知限制：SVG 格式图片无法生成 caption
现象：generate_image_captions() 处理 SVG 格式图片时，DashScope 视觉模型
（qwen3-vl-plus）返回 400 错误："The image format is illegal and cannot
be opened"。
原因：视觉模型只接受位图格式（JPEG/PNG/WebP），SVG 是矢量图格式，无法
直接被模型读取，这是模型能力边界，不是代码 bug。
当前处理：已被 generate_image_captions() 现有的单张失败兜底逻辑正确
拦截，该图片 caption 保留为空字符串，不影响同一 slide 里其他图片，
不影响整体 annotate 流程。
后续：如果发现方案库里 SVG 格式图片占比较高，可考虑引入 SVG 转位图
预处理（如 cairosvg）再传给视觉模型；当前样本量下暂不处理。