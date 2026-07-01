import time
import json
from typing import List, Callable, Optional
from pydantic import BaseModel
from google.genai import types
from concurrent.futures import ThreadPoolExecutor, as_completed

class CleanedTopic(BaseModel):
    title: str
    hierarchy_path: str
    sanitized_content: str  # Clean content without noise

class DocumentCleanPayload(BaseModel):
    topics: List[CleanedTopic]

def split_raw_text_into_large_blocks(text: str, block_size: int = 25000, overlap: int = 2500) -> List[str]:
    """
    Splits raw text into overlapping segments to prevent LLM context and output window saturation.
    
    Args:
        text: The source string.
        block_size: Character size of each chunk.
        overlap: Character size of overlap between chunks.
        
    Returns:
        List[str]: The split string blocks.
    """
    if not text:
        return []
    if len(text) <= block_size:
        return [text]
        
    blocks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = start + block_size
        block = text[start:end]
        blocks.append(block)
        
        # Advance by size minus overlap
        start += (block_size - overlap)
        
        # Guard against zero/negative step size
        if block_size <= overlap:
            break
            
    return blocks

def call_llm_cleaner_api(text_block: str, client, model_name: str) -> str:
    """
    Executes a content generation request to the Gemini API to clean document noise,
    forcing structured JSON schema execution.
    
    Args:
        text_block: Raw chunk of text.
        client: google.genai.Client instance.
        model_name: Name of target model.
        
    Returns:
        str: Cleaned Markdown content reconstructed from structured JSON.
    """
    system_instruction = (
        "You are an expert Knowledge Engineer.\n"
        "Your task is to clean and optimize raw text extracted from documents for ingestion into a RAG system.\n\n"
        "Please process the input text according to these strict rules:\n"
        "1. Identify the logical topics in the text block.\n"
        "2. For each topic, extract a descriptive title and determine its hierarchy_path (e.g. 'Main Section > Sub Section').\n"
        "3. Remove all conversational filler, chatty preambles, introductory welcoming text, repetitive examples, "
        "licensing boilerplate, page headers, footers, and meta-commentary.\n"
        "4. Retain 100% of the raw, granular conceptual rules, specifications, laws, formulas, criteria, definitions, "
        "and precise technical details.\n"
        "5. STOP CONDITION: If the text segment consists of a literal keyword index, subject index, glossary list, or bibliography, output nothing or skip the processing of this block entirely. Do not format indices into markdown chunks.\n"
        "6. Output clean, raw content inside sanitized_content. Do not summarize or lose critical information. "
        "Do not create high-level summary blocks or meta-recaps (e.g., 'Overview' or 'General Best Practices') if the specific technical details and sub-sections are already detailed downstream in the text. Prioritize splitting the document into atomic, standalone concepts."
    )
    
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=text_block,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=DocumentCleanPayload
            )
        )
        if not response.text:
            raise ValueError("Gemini API returned an empty response.")
            
        data = json.loads(response.text)
        markdown_parts = []
        for topic in data.get("topics", []):
            title = topic.get("title", "").strip()
            path = topic.get("hierarchy_path", "").strip()
            content = topic.get("sanitized_content", "").strip()
            
            # Determine heading level from path hierarchy depth
            levels = [p.strip() for p in path.split(">") if p.strip()]
            depth = len(levels)
            if depth == 0 and title:
                depth = 1
            heading_prefix = "#" * max(1, min(6, depth))
            
            topic_md = []
            if title:
                topic_md.append(f"{heading_prefix} {title}")
            if content:
                topic_md.append(content)
                
            if topic_md:
                markdown_parts.append("\n\n".join(topic_md))
                
        return "\n\n".join(markdown_parts)
    except Exception as e:
        raise RuntimeError(f"Gemini API request failed: {str(e)}")

def run_ai_cleaning_pipeline(
    raw_text: str, 
    client, 
    model_name: str, 
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> str:
    """
    Coordinates chunking, API execution, pacing, and assembly of cleaned output text.
    
    Args:
        raw_text: Full raw text from the parsed document.
        client: google.genai.Client instance.
        model_name: Selected Gemini model.
        progress_callback: Optional function invoked with (current_block, total_blocks) for UI updates.
        
    Returns:
        str: Reassembled, cleaned Markdown.
    """
    blocks = split_raw_text_into_large_blocks(raw_text)
    total_blocks = len(blocks)
    if total_blocks == 0:
        return ""
        
    results_map = {}
    completed_count = 0
    
    # Run requests concurrently using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_index = {
            executor.submit(call_llm_cleaner_api, block, client, model_name): idx 
            for idx, block in enumerate(blocks)
        }
        
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                cleaned_block = future.result()
                results_map[idx] = cleaned_block
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, total_blocks)
            except Exception as e:
                # Re-raise thread errors to make sure they propagate to the main thread
                raise e
                
    # Sort reassembled blocks by their original index to preserve document continuity
    sorted_blocks = [results_map[i] for i in range(total_blocks)]
    return "\n\n".join(sorted_blocks)
