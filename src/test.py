"""
方案智能体 - RAG 评测集生成脚本

用途：
    基于已解析的方案文档（MinerU 输出的 .md/.txt 文本），用 RAGAS TestsetGenerator
    自动生成候选问答对，导出 CSV，供人工筛选后作为 DeepEval/RAGAS 评测集使用。

前置依赖（本地执行）：
    pip install ragas langchain-openai pandas --break-system-packages

    注意：桌面上下载的 ragas-main 源码不需要用，直接装发布版就行，除非你以后
    要改 ragas 源码本身（目前不需要）。

环境变量：
    export DASHSCOPE_API_KEY=你现有的 DashScope key（不用另外申请）

使用方法：
    1. 把下面 DOCS_DIR 改成你 MinerU 解析输出的目录
    2. 按需调整 TESTSET_SIZE
    3. python generate_testset.py
    4. 打开生成的 candidate_testset.csv，人工过一遍，删掉答非所问的题，
       剩下的就是你的第一版评测集

已知风险点（第一次跑大概率要调）：
    - DashScope 的 embedding 接口在 OpenAI 兼容模式下的参数/维度是否和
      langchain-openai 的 OpenAIEmbeddings 完全对齐，没有百分百把握，
      第一次跑如果报错，大概率是这里，可以把这段报错丢给本地 CC debug。
    - TestsetGenerator 的具体参数在不同 ragas 版本间有过调整，
      如果 import 或参数报错，先确认 pip show ragas 的版本号，
      再对照对应版本的官方文档改参数名。
"""

import os
from pathlib import Path

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.documents import Document
from ragas.testset import TestsetGenerator

# ------------------- 配置区，按需改 -------------------
DOCS_DIR = Path("./parsed_docs")        # TODO: 改成 MinerU 解析输出目录
TESTSET_SIZE = 30                        # 想要生成的候选问答对数量
OUTPUT_CSV = "candidate_testset.csv"
QWEN_MODEL = "qwen-plus"                 # 生成问题用的模型
QWEN_EMBED_MODEL = "text-embedding-v3"   # DashScope 的 embedding 模型
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# --------------------------------------------------------


def load_documents(docs_dir: Path) -> list[Document]:
    """把解析后的文本/markdown 文件读成 LangChain Document 对象列表。"""
    docs = []
    for fp in docs_dir.glob("**/*"):
        if fp.suffix.lower() not in {".md", ".txt"}:
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        docs.append(Document(page_content=text, metadata={"source": str(fp)}))
    if not docs:
        raise RuntimeError(f"{docs_dir} 下没找到 .md/.txt 文件，检查路径是否正确")
    return docs


def main():
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置环境变量 DASHSCOPE_API_KEY")

    print(f"读取文档目录：{DOCS_DIR}")
    docs = load_documents(DOCS_DIR)
    print(f"共加载 {len(docs)} 份文档")

    # 生成问题用的 LLM，走 DashScope 的 OpenAI 兼容接口，不用 OpenAI key
    generator_llm = ChatOpenAI(
        model=QWEN_MODEL,
        api_key=api_key,
        base_url=DASHSCOPE_BASE_URL,
        temperature=0.7,
    )

    # 构建知识图谱用的 embedding，同样走 DashScope
    generator_embeddings = OpenAIEmbeddings(
        model=QWEN_EMBED_MODEL,
        api_key=api_key,
        base_url=DASHSCOPE_BASE_URL,
    )

    generator = TestsetGenerator(llm=generator_llm, embedding_model=generator_embeddings)

    print(f"开始生成 {TESTSET_SIZE} 条候选问答对，可能需要几分钟...")
    dataset = generator.generate_with_langchain_docs(docs, testset_size=TESTSET_SIZE)

    df = dataset.to_pandas()
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"完成，已导出到 {OUTPUT_CSV}，共 {len(df)} 条，打开人工筛选一遍即可。")


if __name__ == "__main__":
    main()