from mineru import MinerU
import os
from dotenv import load_dotenv
load_dotenv()

client = MinerU(os.environ.get("MINERU_TOKEN", ""))
batch_id = client.submit("files/测试测试.pptx", model="vlm")
print(f"提交成功，batch_id: {batch_id}")

results = client.get_batch(batch_id)
print(f"state: {results[0].state}")
import time

for i in range(6):  # 查6次，每次间隔10秒，一共等1分钟
    time.sleep(10)
    results = client.get_batch(batch_id)
    print(f"第{i+1}次查询, state: {results[0].state}")