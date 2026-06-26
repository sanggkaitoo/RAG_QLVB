import os
import json
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="QLVB Agentic RAG")

qdrant_client = QdrantClient(
    host=os.getenv("QDRANT_HOST", "localhost"), 
    port=int(os.getenv("QDRANT_PORT", 6333)), 
    api_key=os.getenv("QDRANT_API_KEY"),
    https=False
)
embedding_model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"))

openai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)
MAIN_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct:free")

def agent_intent_router(query: str) -> str:
    """Qwen tự xác định Loại văn bản theo Nghị định 30 dựa vào câu hỏi"""
    prompt = f"""
    Câu hỏi: "{query}"
    Nhiệm vụ: Xác định xem người dùng muốn tìm thông tin trong Loại văn bản nào.
    
    Quy tắc phân loại (CHỈ trả về ĐÚNG 1 từ khóa, không giải thích, viết hoa chữ cái đầu):
    - Nếu hỏi về số liệu, kết quả thực hiện, tình hình triển khai, chậm hạn -> Trả về: Báo cáo
    - Nếu hỏi về định hướng, nhiệm vụ phải làm, lộ trình -> Trả về: Kế hoạch
    - Nếu hỏi về văn bản pháp lý, quy chế, nội quy -> Trả về: Quyết định
    - Nếu người dùng nhắc đích danh loại -> Trả về đúng loại đó.
    - Nếu hỏi chung chung -> Trả về: All
    """
    try:
        response = openai_client.chat.completions.create(
            model=MAIN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        if len(content) > 20: return "All" 
        return content
    except Exception as e:
        print(f"Lỗi Router: {e}")
        return "All"

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(os.getenv("BASE_DIR", "."), "src", "bot", "index.html"))

@app.get("/api/search_stream")
async def api_search_stream(q: str = Query(...)):
    target_doc_type = agent_intent_router(q)
    print(f"\n🎯 [ROUTER] Đã phân loại ý định tìm kiếm: [{target_doc_type}]")
    
    query_vector = embedding_model.encode(q).tolist()
    search_filter = None
    
    # Chỉ Filter khi Router trả về các thể loại rõ ràng (Bỏ qua 'All' hoặc rác)
    if target_doc_type.lower() not in ["all", "khác", "khac", "none"] and len(target_doc_type) < 15:
        search_filter = Filter(
            must=[FieldCondition(key="loai_van_ban", match=MatchValue(value=target_doc_type))]
        )
    
    # Tăng limit lên 40 để AI có góc nhìn tổng quan hơn khi đếm số lượng
    SEARCH_LIMIT = 40 
    
    search_results = qdrant_client.search(
        collection_name=os.getenv("QDRANT_COLLECTION", "qlvb_docs"),
        query_vector=query_vector,
        query_filter=search_filter,
        limit=SEARCH_LIMIT 
    )
    
    # --- CƠ CHẾ SMART FALLBACK (Dự phòng thông minh) ---
    if len(search_results) == 0 and search_filter is not None:
        print(f"⚠️ [CẢNH BÁO] Không tìm thấy '{target_doc_type}' nào khớp. Đang gỡ bỏ bộ lọc và quét toàn bộ CSDL...")
        search_results = qdrant_client.search(
            collection_name=os.getenv("QDRANT_COLLECTION", "qlvb_docs"),
            query_vector=query_vector,
            limit=SEARCH_LIMIT
        )
    # --------------------------------------------------

    print(f"🔎 [RETRIEVE] Đã trích xuất thành công {len(search_results)} đoạn văn bản đưa vào Context.")
    
    context_text = ""
    retrieved_contexts = []
    
    for idx, hit in enumerate(search_results):
        text = hit.payload.get("text", "")
        meta = hit.payload
        retrieved_contexts.append({"text": text, "metadata": meta})
        tags_str = ", ".join(meta.get('tags', []))
        context_text += f"\n[Nguồn {idx+1}] (Loại: {meta.get('loai_van_ban')} | Tags: {tags_str})\n{text}\n"

    def generate_stream():
        yield f"[SOURCES]{json.dumps(retrieved_contexts)}[/SOURCES]"
        
        system_prompt = """Bạn là trợ lý tham mưu tổng hợp báo cáo và dữ liệu chuyên nghiệp.
        Nhiệm vụ: Phân tích các đoạn văn bản để trả lời câu hỏi của người dùng.
        
        QUY TẮC BẮT BUỘC:
        1. CHỈ trả lời dựa vào dữ liệu trong phần CÁC ĐOẠN VĂN BẢN LIÊN QUAN.
        2. Nếu thông tin không đầy đủ, hãy tổng hợp những gì đang có và nói rõ: "Dữ liệu hiện tại chỉ ghi nhận được..." thay vì từ chối trả lời hoàn toàn.
        3. Mọi ý kiến tổng hợp, con số, số liệu PHẢI trích dẫn nguồn theo cú pháp [Nguồn X].
        """
        
        try:
            stream_response = openai_client.chat.completions.create(
                model=MAIN_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"CÁC ĐOẠN VĂN BẢN LIÊN QUAN:\n{context_text}\n\nCÂU HỎI: {q}"}
                ],
                temperature=0.2,
                stream=True
            )
            for chunk in stream_response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n\n[Lỗi kết nối AI]: {str(e)}"

    return StreamingResponse(generate_stream(), media_type="text/event-stream")