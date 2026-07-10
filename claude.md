# 方案知识库项目

## 项目背景

康晋电气内部 demo，供方案部门试用。公司有大量历史电气工程方案（PPTX 格式），目前靠人工翻找。目标是把这些方案入库，支持语义检索，并辅助生成新方案。

定位：内部 demo 优先，不是生产系统。
- 不需要多用户、权限管理、高并发
- 不需要完整错误处理和监控
- 优先跑通核心流程，代码够用即可
- 后期确认有价值再加工程化

## 项目目标
把历史电气工程方案存成可检索的知识库，支持生成新方案

## 当前阶段
入库阶段已完成（文字 + 图片 + 版本控制），生成阶段已基本完成（检索+生成全链路跑通、引用防幻觉已上线），剩余：方案类型过滤 bug 修复 + Markdown 文件持久化

## 技术栈
- 文档解析：MinerU云API（见 docs/mineru_api.md）
- 向量库：Chroma（本地）
- 生成：DashScope Qwen（qwen3.7-max，与 embedding 同一供应商），通过 adapters/llm_client.py 调用
- 输出格式：Markdown

## 项目结构
Core/
  claude.md         # 本文件，项目说明
  docs/             # 参考文档（API 规范、数据格式，供开发参考）
  files/            # 待解析的本地 PPTX 文件（运行时输入，不提交 git）
  src/
    adapters/
      parser.py         # MinerU 云 API 封装
      vector_store.py   # Chroma 封装
      llm_client.py     # LLM API 封装（可换模型）
    main.py

## 数据流
PPTX 文件
  → parser.py（MinerU 解析，同时提取图片到本地 images/ 目录）
  → list[SlideDict]  每个 slide: {slide_number, title, content, source_file, images}
     images: [{"path": 本地图片路径, "caption": 图片描述（可能为空）}]
  → vector_store.py（Chroma 入库，按 file_hash 判断版本、images 序列化存 metadata）
  → 检索时返回相关 slide（含文字 + 关联图片素材）
  → llm_client.py（生成新方案段落）

## 关键设计决策
- 按 slide 切分：每张幻灯片作为一个独立的 Chroma document，不合并整份文件
- adapters 层可替换：MinerU / Chroma / LLM 都封装在 adapters 下，外部代码不直接 import 这些库
- 单一入口：main.py 是唯一的业务逻辑入口，adapters 只做 IO 封装
- **图片提取但不强制要求 caption**：图片文件统一提取到本地 `images/<pptx文件名>/` 目录，不管
  MinerU 有没有识别出 caption 都保留；只有 caption 非空时才拼进正文参与语义检索，避免"没有
  说明文字的图片被检索逻辑忽略、方案员搜到 slide 却拿不到对应图片"的情况
- **版本控制用文件哈希**：ingest 前先算 pptx 的 MD5，跟 Chroma 里记录的上次入库哈希比对——
  一致就跳过（不重复调用 MinerU / embedding，省钱），不一致才重新解析。解析成功后才清空旧
  版本、插入新版本（不是先删再解析），避免解析中途失败导致新旧数据都丢的中间态
- **DashScope embedding 分批 + 重试**：单次请求最多 10 条文本，超过会报错；`__call__` 内部
  自动按 10 条分批调用，批次间隔 0.2s 防限流，遇到 429/5xx 自动重试最多 3 次
- **debug_zips/ 按源文件名保存**：MinerU 返回的原始 zip 包保存到 `debug_zips/<pptx文件名>.zip`
  方便排查解析问题；同名文件重新解析会覆盖（不留历史版本），不同文件互不覆盖

## 编码规范
- 可替换依赖封装在 /src/adapters/ 下
- 框架和标准库直接import
- adapters外的代码不直接import向量库/LLM/解析库
- 复杂逻辑处加中文注释说明意图
- 每个class写一行中文docstring说明职责
- 函数参数和返回值写类型标注

## 目录说明
- files/：待解析的本地 PPTX 文件（运行时输入，不提交到 git）
- docs/：项目参考文档（API 规范、数据格式等，供开发参考）

## 环境
- Python 环境：标准 venv
- LLM 调用走阿里云 DashScope 代理（兼容 OpenAI 接口）
- MinerU API Key 从环境变量 MINERU_API_KEY 读取

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
- [ ] 已知 bug 待修：方案类型过滤失效（#3）— 见下方"已知问题"章节


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

## MinerU 接入细节
- **`is_ocr: False`**：PPTX 文字是矢量的，不需要 OCR；
  如果后期处理扫描版 PDF 再开
- **`base_url` 单独存**：所有接口共享同一个前缀，
  换环境或域名只改一个地方
- **上传用二进制模式 `"rb"`**：PPTX 是二进制格式，
  文本模式读取会损坏文件
- **按 `page_idx` 分组，不用 Markdown 分隔符**：MinerU 对 PPTX 输出的 Markdown 是连续的
  （标题用 "##"，没有 "---" 分隔符），按分隔符切分不可靠；改用 `content_list.json` 里每个
  block 自带的 `page_idx` 字段分组，稳定得多

## MinerU Cloud API 约束（精准解析）
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

## 检索策略设计（已确定，2026-07）

### 入库阶段新增
- Slide 级 context 增强：每张 slide 单独调用一次 LLM 生成上下文摘要
  （属于哪份方案、大致章节位置），prepend 到 embedding 文本前存入
  Chroma。入库是一次性批处理，调用次数不敏感，不做文档级摊薄。
- 已知局限：当前图片 caption 解析暂缓，slide 文字信息本身有限时，
  生成的 context 摘要信息量可能不足，属于阶段性妥协。后续图片理解
  能力补上后，可利用现有 hash 版本控制机制强制刷新重新生成 context，
  不需要改动版本控制逻辑。
- Metadata 打标：方案类型、客户行业等结构化字段，搭 context 生成
  同一次 LLM 调用一起做结构化输出，存入 Chroma metadata。

### 检索阶段流程（阶段二实现顺序）
1. Query 改写：需求描述 → 1-3 个检索用 query
2. metadata 过滤（如已打标，按类型圈定范围）
3. 向量检索（纯语义，Chroma），取候选
4. 相邻 slide 扩展：命中 slide 前后各带 1-2 张

### 明确推迟，验证后再加
- Hybrid 检索（BM25）：<100 份文档规模下，纯语义检索够用，且遇到
  漏检人工也能兜底。仅当后续验证发现型号/编号类精确匹配确实漏检
  时才加，加则优先换原生支持 hybrid 的向量库，不给 Chroma 外挂
  rank_bm25。
- Rerank：同理先不加，后续启用时用 DashScope qwen3-rerank
  （gte-rerank 已于 2026-05-30 下线）。

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

### 已知问题与对应代码位置索引

| 问题 # | 涉及文件 | 关键行 | 说明 |
|--------|----------|--------|------|
| 1. 表格解析 | `parser.py` | 253-260 | `_render_page` 中 table_body 直接 `str(cell).strip()` 拼接，无格式清洗 |
| 2. 引用幻觉 | `pipeline.py` | 21-28 | ~~`_GENERATION_SYSTEM_PROMPT` 未传可引用编号白名单~~ **已修（2026-07）**：prompt 已注入编号白名单 + 生成后正则校验 |
| 3. 类型过滤 | `vector_store.py` | 409 | `_build_where` 用 `{"proposal_type": proposal_type}` 精确匹配 |
| 3. 类型过滤 | `pipeline.py` | 230-239 | fallback 掩盖了过滤失败 |
| 4. 图片描述 | `parser.py` | 262-275 | `image_caption` 直取 MinerU 原始值 |
| 5. 引用溯源 UI | 未实现 | — | 需前端（Gradio/Web）支持 |
| 7. PDF 解析 | `parser.py` | 全文件 | 仅 `parse_pptx()` 一个公共解析方法 |

### 待确认的细节（开发时发现）

- `parser.py:14` import 的是 `from mineru import MinerU`，而 `pyproject.toml` 依赖写的是 `mineru-open-sdk>=0.1`，需确认实际安装的是哪个包，两者是否一致
- `test.py` 是临时调试脚本（硬编码 `"files/测试测试.pptx"`），不属于正式测试套件，建议清理或转为正式测试

## 已知问题与优化计划（2026-07 记录）

### 1. 表格解析潜在数据丢失问题
- **现象**：MinerU 输出的部分表格（如"储能系统收益表"）在 Markdown /
  `content_list.json` 转换后表现为逐字符竖排的乱码格式，单元格内容
  可能被拆成单字纵向排列。
- **待办**：先核查 `content_list.json` 原始数据中该表格的 `table_body`
  单元格内容是否完整——若数据本身已缺失，需评估改用 `python-pptx` 直接
  读取 pptx 内部表格 XML，而非依赖 MinerU 的 Markdown 转换；若原始数据
  完整只是序列化格式问题，做正则 / 后处理清洗即可。
- **优先级**：高（直接关系到生成内容中具体数字的可信度）。

### 2. 引用溯源存在幻觉（citation hallucination）
- **现象**：一次实测查询（"光伏储能一体化方案"）中，最终生成稿引用了
  Slide 3、Slide 4、Slide 9，但这三个编号从未出现在该次检索日志的主结果
  或相邻扩展结果中（实际检索到并送入生成上下文的 slide 集合为
  1,2,5,6,7,8,10-21）。说明生成模型在编造引用编号，而非从真实检索到的
  内容中选择。生成 prompt（`pipeline.py:_GENERATION_SYSTEM_PROMPT`）仅要求
  `[来源: xxx.pptx Slide N]` 格式标注，未列出可引用的编号集合，也未做
  输出后校验。
- **待办**：
  - 在生成 prompt 中明确列出本次上下文里实际可引用的 slide 编号集合
    （含相邻扩展的 slide），约束模型只能从中选择，不能自行生成不存在的
    编号；
  - 生成后增加一道校验：正则提取输出中的所有 "Slide N" 编号，与实际
    传入 context 的编号集合做差集比对，发现不在集合内的编号需拦截、
    重新生成或明确标记为不可信引用。
- **优先级**：高。是后续"引用可点击跳转溯源"功能的前置依赖，必须先修复。

### 3. 方案类型过滤实际从未生效
- **现象**：query 改写阶段（`llm_client.py:rewrite_query`）推测的方案类
  型为"光储"，但库中实际存储的 `proposal_type` 字段值为"光储充" /
  "光储充一体化"。`vector_store.py:_build_where` 中过滤使用精确字符串匹配
  （`{"proposal_type": proposal_type}`），从未命中，导致每次都触发 fallback
  （不限类型重新检索，`pipeline.py:230-239`）。过滤功能形同虚设，但被
  fallback 掩盖，未被察觉。
- **待办**：改为包含匹配（判断推测类型是否为存储类型的子串，或反之），或
  建立类型同义词归一化表（如"光储"→"光储充"）。
- **优先级**：中。

### 4. 图片描述方案：降低纯手工标注成本
- **方向**：
  - 让 LLM 生成图片描述时，同时提供该图片所在 slide 的文字上下文，使
    描述带有语用信息（例如"该图用于证明某项目的真实部署"），而非孤立的
    纯视觉描述；
  - 建立图片角色分类体系（如：项目实景图 / 系统架构图 / 产品实物图 /
    数据图表 / 收益测算表），让模型做分类而非自由文本描述；分类置信度
    低时再人工抽查确认，把工作量从"全量手动"降到"抽查"。
- **当前状态**：图片 caption 直接取 MinerU 返回的 `image_caption`
  （`parser.py:263`），无角色分类、无语用上下文生成。

### 5. Slide 内容存储粒度与生成简繁应分离；规划"引用可点击溯源"功能
- **原则**：系统定位是"从历史方案中提取素材与方向，供人工拼装成稿"，
  因此存储 / 检索层的 slide 内容应保留完整原始细节（具体数字、案例名称、
  参数），不应过度精简；生成层的叙述性文字可以保持相对精炼，两者是不同
  层级，不应用同一套"详略"标准去衡量。当前 `vector_store.py:add_slides`
  中 title + content 全文存入 document 文本，未做精简，符合这一原则。
- **规划功能**：引用编号（如 "Slide N"）应可点击或悬停跳转，展示该
  slide 的原始文字内容及关联图片（不必还原为 PPT 视觉样式，做成一张信息
  卡片即可）。该功能强依赖第 2 条（引用溯源幻觉）先修复，否则跳转目标
  不可信。

### 6. 图片入库与溯源关联方式
- **方案**：图片作为其所属 slide 记录的附属字段存储（图片路径 + 角色标
  签 + 上下文关联描述），检索到该 slide 时图片跟随一起返回，而不是为图
  片单独建立可独立检索的向量索引。当前实现中 `images` 字段已序列化为
  JSON 字符串存入 slide metadata（`vector_store.py:185`），检索时通过
  `_slide_from_meta` 还原（line 429-433），与该方案方向一致。独立图片
  检索（例如"给我一张储能柜实拍图"这类 query）复杂度高很多，且目前没有
  验证过的真实需求，先不做，等有真实使用反馈后再评估是否需要。

### 7. 新增 PDF 解析支持
- **背景**：MinerU 目前已原生支持 PDF / DOCX / PPTX / XLSX 解析
  （3.1.0 版本起），PDF 是其最早、最成熟的解析场景。理论上可以复用现有
  adapter 架构（`parser.py`）新增 PDF 输入通道，预期工作量小于当初适配
  PPTX 的成本。
- **备注**：近期使用的 MinerU 官网云端 API 出现连续故障，需先确认是账号 /
  网络问题还是服务方问题，再排期这项开发。

### 8. 当前推进节奏说明
- 决策详情见 DECISIONS.md。

## 架构决策：大纲中间表示与导出解耦（v1）

- 生成模块的输出应为格式无关的结构化对象（IR），不掺任何pptx/docx专用代码：
  [{level, heading, bullets, citations: [{source_file, slide_index}]}, ...]
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

## 测算模块（光储充）价值边界的判断标准
- 详情见 DECISIONS.md。

## claude.md 使用边界

- claude.md 应只保留"写代码需要的信息"：架构决策、目录约定、已知坑点。
- 产品定位讨论、市场部沟通记录、方向权衡等决策历史，建议迁移至独立文件
  （如 docs/product-log.md 或 DECISIONS.md），claude.md 中留一行索引即可，
  避免每次代码会话加载无关上下文、且降低长期维护成本。