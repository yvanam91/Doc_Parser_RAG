import re
import string
from typing import List, Dict, Any

# Compile pattern to match prepended [Context: ...] context tag prefixes
CONTEXT_PREFIX_RE = re.compile(r'^\[Context:[^\]]*\]\n?', re.IGNORECASE)

def deduplicate_chunks(chunks: List[Dict[str, Any]], similarity_threshold: float = 0.75) -> List[Dict[str, Any]]:
    """
    Deduplicates content-redundant chunks using Jaccard Similarity (word intersection over union),
    preserving the chunk with the deepest/longest hierarchical header_path context.
    
    Args:
        chunks: List of generated chunks.
        similarity_threshold: Jaccard overlap threshold to flag duplicates.
        
    Returns:
        List[Dict[str, Any]]: Deduplicated list of chunks.
    """
    clean_chunks = []
    
    for chk in chunks:
        # Strip context prefix block to compare raw contents
        raw_chk = CONTEXT_PREFIX_RE.sub('', chk["content"])
        # Replace punctuation characters with whitespace
        cleaned_chk = "".join(c if c not in string.punctuation else " " for c in raw_chk)
        words_chk = set(cleaned_chk.lower().split())
        is_duplicate = False
        
        for saved_chk in clean_chunks:
            raw_saved = CONTEXT_PREFIX_RE.sub('', saved_chk["content"])
            cleaned_saved = "".join(c if c not in string.punctuation else " " for c in raw_saved)
            words_saved = set(cleaned_saved.lower().split())
            
            # Compute semantic Jaccard similarity
            intersection = words_chk.intersection(words_saved)
            union = words_chk.union(words_saved)
            similarity = len(intersection) / len(union) if union else 0.0
            
            if similarity > similarity_threshold:
                is_duplicate = True
                # Favor the deepest, most nested header path
                if len(chk["header_path"]) > len(saved_chk["header_path"]):
                    saved_chk["content"] = chk["content"]
                    saved_chk["header_path"] = chk["header_path"]
                    saved_chk["header"] = chk["header"]
                    saved_chk["word_count"] = chk["word_count"]
                    saved_chk["char_count"] = chk["char_count"]
                break
                
        if not is_duplicate:
            clean_chunks.append(chk)
            
    return clean_chunks

# Compile regex to target horizontal rulers (---) or dangling hyphens on standalone lines
CLEAN_PATTERN = re.compile(r'^\s*(?:-{3,}|-)\s*$', re.MULTILINE)

def chunk_markdown_text(cleaned_text: str) -> List[Dict[str, Any]]:
    """
    Chunks markdown text semantically based on header hierarchy (# to ######).
    
    Args:
        cleaned_text (str): Cleaned markdown text.
        
    Returns:
        List[Dict[str, Any]]: List of semantic chunks containing text and metadata.
    """
    chunks = []
    lines = cleaned_text.splitlines()
    
    # Track hierarchy: level -> header name
    current_headers = {1: None, 2: None, 3: None, 4: None, 5: None, 6: None}
    current_chunk_lines = []
    
    def save_chunk(headers_dict, text_lines):
        text_content = "\n".join(text_lines).strip()
        if not text_content:
            return
            
        # Strip formatting artifacts (rulers, dangling hyphens, and extreme padding)
        cleaned_content = CLEAN_PATTERN.sub('', text_content).strip()
        if not cleaned_content:
            return
            
        # Create a breadcrumb-style header path (e.g. "Root > Section > Subsection")
        path_parts = []
        for level in sorted(headers_dict.keys()):
            h = headers_dict[level]
            if h:
                path_parts.append(h)
                
        header_path = " > ".join(path_parts) if path_parts else "Document Root"
        header_text = path_parts[-1] if path_parts else "Root"
        
        # Architectural safety filter to ignore textbook indexes and bibliographies
        ignored_terms = ["subject index", "index", "bibliography", "references"]
        header_path_lower = header_path.lower()
        header_text_lower = header_text.lower()
        if any(term in header_path_lower or term in header_text_lower for term in ignored_terms):
            return
            
        # Prepend context path directly to chunk body
        injected_content = f"[Context: {header_path}]\n{cleaned_content}"
        
        chunks.append({
            "header": header_text,
            "header_path": header_path,
            "content": injected_content,
            "word_count": len(injected_content.split()),
            "char_count": len(injected_content)
        })

    for line in lines:
        # Check if line is a markdown header
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            # Save preceding chunk
            save_chunk(current_headers, current_chunk_lines)
            current_chunk_lines = []
            
            # Parse level and text
            level = len(match.group(1))
            header_text = match.group(2).strip()
            
            # Update current level and reset all lower hierarchy levels
            current_headers[level] = header_text
            for l in range(level + 1, 7):
                current_headers[l] = None
        else:
            current_chunk_lines.append(line)
            
    # Save the final chunk remaining
    save_chunk(current_headers, current_chunk_lines)
    
    # Fallback if no markdown headers were detected
    if not chunks and cleaned_text.strip():
        cleaned_content = CLEAN_PATTERN.sub('', cleaned_text).strip()
        if cleaned_content:
            header_path = "Document Root"
            injected_content = f"[Context: {header_path}]\n{cleaned_content}"
            chunks.append({
                "header": "Root",
                "header_path": header_path,
                "content": injected_content,
                "word_count": len(injected_content.split()),
                "char_count": len(injected_content)
            })
        
    return deduplicate_chunks(chunks)
