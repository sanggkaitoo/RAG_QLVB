import os
import json
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="QLVB AI Web API")

qdrant_client = QdrantClient(
    host=os.getenv("QDRANT_HOST", "localhost"), 
    port=int(os.getenv("QDRANT_PORT", 6333)), 
    api_key=os.getenv("QDRANT_API_KEY"),
    https=False
)

embedding_model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"), device=os.getenv("EMBEDDING_DEVICE", "cpu"))
openai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))

SYSTEM_PROMPT = """Bạn là trợ lý pháp lý của cơ quan nhà nước.
QUY TẮC TUYỆT ĐỐI:
1. CHỈ trả lời dựa vào các đoạn văn bản được cung cấp dưới đây.
2. Nếu thông tin không có trong văn bản, trả lời: "Không tìm thấy thông tin trong kho dữ liệu."
3. MỖI thông tin trích dẫn PHẢI kèm nguồn bằng cách ghi đúng mã số đoạn ở cuối câu, ví dụ: [Đoạn 1], [Đoạn 2]. TUYỆT ĐỐI KHÔNG tự viết dài dòng thêm thông tin khác vào trong ngoặc vuông.
4. KHÔNG suy đoán, KHÔNG bổ sung kiến thức bên ngoài.
5. Sử dụng định dạng Markdown (**, -, *) để làm nổi bật thông tin quan trọng.

CÁC ĐOẠN VĂN BẢN LIÊN QUAN:
{context}
"""

@app.get("/")
async def read_index():
    return FileResponse("/opt/qlvb_ai/src/bot/index.html")

@app.get("/api/search_stream")
async def api_search_stream(q: str = Query(...)):
    query_vector = embedding_model.encode(q).tolist()
    
    search_results = qdrant_client.search(
        collection_name=os.getenv("QDRANT_COLLECTION", "qlvb_docs"),
        query_vector=query_vector,
        limit=4
    )
    
    context_text = ""
    retrieved_contexts = []
    
    for idx, hit in enumerate(search_results):
        text = hit.payload.get("text", "")
        meta = hit.payload
        retrieved_contexts.append({"text": text, "metadata": meta})
        context_text += f"\n[Đoạn {idx+1}] {text}\n"

    def generate_stream():
        yield f"[SOURCES]{json.dumps(retrieved_contexts)}[/SOURCES]"
        
        stream_response = openai_client.chat.completions.create(
            model=os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(context=context_text)},
                {"role": "user", "content": f"CÂU HỎI: {q}"}
            ],
            temperature=0.1,
            stream=True
        )
        
        for chunk in stream_response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return StreamingResponse(generate_stream(), media_type="text/event-stream")
