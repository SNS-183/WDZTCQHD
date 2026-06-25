import logging
import re
import time
from collections import Counter
from typing import Iterable

import jieba.posseg as pseg
import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer


logger = logging.getLogger(__name__)

CN_STOP = set(
    """
    的 了 和 与 及 或 就 在 是 为 对 于 并 而 把 被 有 无 中 上 下 其中 以及 通过 关于 由于
    这 那 他 她 它 我 你 您 他们 她们 我们 你们 这些 那些 该 本 其
    也 都 很 更 最 还 又 已 已经 正在 曾 经 常 常常 以前 现在 近日 不少 如果 因此 以及
    并且 而且 因为 所以 即使 同时 目前 近日 近期 当下 过去 未来 今年 去年 明年 本月
    """.split()
)

ALLOWED_POS = {"n", "nt", "nz", "vn", "v", "eng"}
GENERIC_THEME_SUFFIXES = ["主题", "治理", "服务", "发展"]


def guess_language(text: str) -> str:
    """非常粗糙的语言检测：中文字符比例 > 0.3 就当中文。"""
    if not text:
        return "unknown"
    total = len(text)
    cn = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    ratio = cn / max(total, 1)
    return "zh" if ratio > 0.3 else "en"


def split_sentences(text: str) -> list[str]:
    """按中文常见句读符切句。"""
    if not text:
        return []
    parts = re.split(r"[。！？!?；;\n\r]+", str(text))
    return [part.strip() for part in parts if str(part).strip()]


def parse_extract_texts(request_data: dict):
    """解析 /extract 的文本输入，保持现有兼容行为。"""
    if "text" in request_data:
        return [request_data["text"]], None
    if "texts" in request_data and isinstance(request_data["texts"], list):
        texts = request_data["texts"]
        if not texts:
            return None, "参数错误：文本内容不能为空"
        return texts, None
    return None, "参数错误：请提供 'text' 字段或 'texts' 数组"


def parse_wordcloud_texts(request_data: dict):
    """解析词云输入；词云模式对空白文本更严格。"""
    if "text" in request_data:
        text = request_data.get("text")
        if text is None or not str(text).strip():
            return None, "参数错误：text 不能为空"
        return [str(text)], None

    if "texts" in request_data and isinstance(request_data.get("texts"), list):
        raw_texts = request_data.get("texts") or []
        texts = [str(item) for item in raw_texts if item is not None and str(item).strip()]
        if not texts:
            return None, "参数错误：texts 不能为空"
        return texts, None

    return None, "参数错误：请提供 'text' 字段或 'texts' 数组"


def parse_wordcloud_params(request_data: dict):
    """解析并校验词云参数。"""
    topk = request_data.get("topk", 50)
    min_len = request_data.get("min_len", 2)
    max_len = request_data.get("max_len", 6)
    allowed_pos = request_data.get("allowed_pos", ["n", "nt", "nz", "vn", "v", "eng"])
    remove_substrings = request_data.get("remove_substrings", False)
    with_evidence = request_data.get("with_evidence", False)
    focus_keywords = request_data.get("focus_keywords", [])
    focus_theme = str(request_data.get("focus_theme", "") or "").strip()
    has_evidence_topn = "evidence_topn" in request_data
    evidence_topn = request_data.get("evidence_topn")

    try:
        topk = int(topk)
        min_len = int(min_len)
        max_len = int(max_len)
    except Exception:
        return None, "参数错误：topk/min_len/max_len 必须为整数"

    if has_evidence_topn:
        try:
            evidence_topn = int(evidence_topn)
        except Exception:
            return None, "参数错误：evidence_topn 必须为整数"

    if not isinstance(allowed_pos, list) or not all(isinstance(item, str) for item in allowed_pos):
        return None, "参数错误：allowed_pos 必须为字符串数组"
    if not isinstance(focus_keywords, list) or not all(isinstance(item, str) for item in focus_keywords):
        return None, "参数错误：focus_keywords 必须为字符串数组"
    if not isinstance(remove_substrings, bool):
        return None, "参数错误：remove_substrings 必须为布尔值"
    if not isinstance(with_evidence, bool):
        return None, "参数错误：with_evidence 必须为布尔值"
    if not (10 <= topk <= 200):
        return None, "参数错误：topk 需在 10~200 之间"
    if not (1 <= min_len <= 6):
        return None, "参数错误：min_len 需在 1~6 之间"
    if not (2 <= max_len <= 10):
        return None, "参数错误：max_len 需在 2~10 之间"
    if max_len < min_len:
        return None, "参数错误：max_len 不能小于 min_len"

    if has_evidence_topn:
        if not (1 <= evidence_topn <= 100):
            return None, "参数错误：evidence_topn 需在 1~100 之间"
    else:
        evidence_topn = min(topk, 60)

    return {
        "topk": topk,
        "min_len": min_len,
        "max_len": max_len,
        "allowed_pos": allowed_pos,
        "remove_substrings": remove_substrings,
        "with_evidence": with_evidence,
        "evidence_topn": evidence_topn,
        "focus_keywords": [str(item).strip() for item in focus_keywords if str(item).strip()],
        "focus_theme": focus_theme,
    }, None


def parse_extract_params(request_data: dict):
    """解析 /extract 的主题提取参数。"""
    defaults = {
        "topic_k": 5,
        "topn_keywords": 10,
        "granularity": "sentence",
        "with_evidence": True,
        "evidence_topn": 30,
        "return_topics": True,
        "return_matrix": True,
        "normalize_score": True,
        "debug": False,
    }
    params = dict(defaults)
    for key in defaults:
        if key in request_data:
            params[key] = request_data.get(key)

    try:
        params["topic_k"] = int(params["topic_k"])
        params["topn_keywords"] = int(params["topn_keywords"])
        params["evidence_topn"] = int(params["evidence_topn"])
    except Exception:
        return None, "参数错误：topic_k/topn_keywords/evidence_topn 必须为整数"

    bool_fields = ["with_evidence", "return_topics", "return_matrix", "normalize_score", "debug"]
    for field in bool_fields:
        if not isinstance(params[field], bool):
            return None, f"参数错误：{field} 必须为布尔值"

    if params["granularity"] not in {"sentence", "doc"}:
        return None, "参数错误：granularity 仅支持 sentence 或 doc"
    if not (1 <= params["topic_k"] <= 30):
        return None, "参数错误：topic_k 需在 1~30 之间"
    if not (3 <= params["topn_keywords"] <= 50):
        return None, "参数错误：topn_keywords 需在 3~50 之间"
    if not (1 <= params["evidence_topn"] <= 200):
        return None, "参数错误：evidence_topn 需在 1~200 之间"
    return params, None


def parse_request_file_names(request_data: dict, text_count: int):
    """解析可选的 file_names 参数。"""
    raw_file_names = request_data.get("file_names")
    if raw_file_names is None:
        return ["" for _ in range(text_count)], None
    if not isinstance(raw_file_names, list):
        return None, "参数错误：file_names 必须为数组"

    file_names = []
    for idx in range(text_count):
        raw_name = raw_file_names[idx] if idx < len(raw_file_names) else ""
        normalized_name = str(raw_name).strip() if raw_name is not None else ""
        file_names.append(normalized_name)
    return file_names, None


def _is_chinese_token(token: str) -> bool:
    return bool(token) and all("\u4e00" <= ch <= "\u9fff" for ch in token)


def _tokenize(text: str, min_len: int = 2, max_len: int = 5):
    try:
        tokens = []
        for word, flag in pseg.lcut(text):
            word = word.strip()
            if not word:
                continue
            if not _is_chinese_token(word):
                continue
            if not (min_len <= len(word) <= max_len):
                continue
            if word in CN_STOP:
                continue
            if flag not in ALLOWED_POS:
                continue
            tokens.append(word)

        pattern = r"[\u4e00-\u9fff]{" + str(min_len) + r"," + str(max_len) + r"}"
        extra = re.findall(pattern, text)
        seen = set(tokens)
        for item in extra:
            if item in seen or item in CN_STOP:
                continue
            if not (min_len <= len(item) <= max_len):
                continue
            flag = None
            segs = list(pseg.lcut(item))
            if len(segs) == 1 and segs[0].word == item:
                flag = segs[0].flag
            if flag and flag in ALLOWED_POS:
                tokens.append(item)
                seen.add(item)

        if tokens:
            return tokens
    except Exception:
        logger.exception("分词失败，回退到正则抽词")

    pattern = r"[\u4e00-\u9fff]{" + str(min_len) + r"," + str(max_len) + r"}"
    return [word for word in re.findall(pattern, text) if word not in CN_STOP]


def _build_tfidf(sentences, min_len: int, max_len: int = 5):
    vectorizer = TfidfVectorizer(
        tokenizer=lambda item: _tokenize(item, min_len=min_len, max_len=max_len),
        lowercase=False,
    )
    try:
        tfidf = vectorizer.fit_transform(sentences)
        return vectorizer, tfidf
    except ValueError:
        return None, None


def _rank_terms(vectorizer, tfidf):
    if vectorizer is None or tfidf is None:
        return [], {}
    terms = vectorizer.get_feature_names_out()
    if not len(terms):
        return [], {}
    term_scores = tfidf.sum(axis=0).A1
    pairs = sorted(zip(terms, term_scores), key=lambda item: item[1], reverse=True)
    return [term for term, _ in pairs if term], {term: score for term, score in pairs}


def _dedupe_substrings(candidates):
    filtered = []
    for word in candidates:
        if not word:
            continue
        if any(other != word and len(other) > len(word) and word in other for other in candidates):
            continue
        filtered.append(word)
    return filtered


def _filter_substring_words(counter_map):
    words = list(counter_map.keys())
    filtered = {}
    for word in words:
        if any(other != word and len(other) > len(word) and word in other for other in words):
            continue
        filtered[word] = counter_map[word]
    return Counter(filtered)


def extract_wordcloud_words(texts, topk, min_len, max_len, allowed_pos, remove_substrings):
    counts = Counter()
    allowed_pos = set(allowed_pos)
    for text in texts:
        try:
            for word, flag in pseg.lcut(text):
                word = word.strip()
                if not word:
                    continue
                if not _is_chinese_token(word):
                    continue
                if word in CN_STOP:
                    continue
                if not (min_len <= len(word) <= max_len):
                    continue
                if flag not in allowed_pos:
                    continue
                counts[word] += 1
        except Exception:
            continue

    if not counts:
        return [], counts

    if remove_substrings:
        counts = _filter_substring_words(counts)
        if not counts:
            return [], counts

    pairs = counts.most_common(topk)
    max_count = pairs[0][1] if pairs else 1
    words = []
    for word, count in pairs:
        weight = round(count / max_count, 4) if max_count > 0 else 0.0
        words.append({"text": word, "weight": weight, "count": int(count)})

    for item in words:
        if item["count"] == max_count:
            item["weight"] = 1.0
    return words, counts


def _build_focus_texts(texts, focus_keywords):
    clean_keywords = [str(item).strip() for item in (focus_keywords or []) if str(item).strip()]
    if not clean_keywords:
        return list(texts), 0

    focused_sentences = []
    for text in texts:
        sentences = split_sentences(text)
        if not sentences:
            sentences = [text]
        for sentence in sentences:
            if any(keyword in sentence for keyword in clean_keywords):
                focused_sentences.append(sentence)

    if not focused_sentences:
        return list(texts), 0

    return focused_sentences, len(focused_sentences)


def build_evidence_map(texts, words, topn):
    full_text = "\n".join(texts)
    sentences = split_sentences(full_text)
    evidence_map = {}
    for item in words[:topn]:
        word = item.get("text", "")
        if not word:
            continue

        sentence_index = 0
        evidence = ""
        fallback = False

        for idx, sentence in enumerate(sentences):
            if word in sentence:
                sentence_index = idx + 1
                evidence = sentence
                break

        if sentence_index == 0 and sentences:
            normalized_word = re.sub(r"[\s\u3000\W_]+", "", word)
            if normalized_word:
                for idx, sentence in enumerate(sentences):
                    normalized_sentence = re.sub(r"[\s\u3000\W_]+", "", sentence)
                    if normalized_word in normalized_sentence:
                        sentence_index = idx + 1
                        evidence = sentence
                        break

        if sentence_index == 0:
            fallback = True
            if sentences:
                sentence_index = 1
                evidence = sentences[0]

        if len(evidence) > 80:
            evidence = evidence[:80] + "..."

        evidence_map[word] = {
            "sentence_index": int(sentence_index),
            "evidence": evidence,
            "fallback": bool(fallback),
        }
    return evidence_map


def build_wordcloud_data(texts, params):
    """构建词云响应数据。"""
    focus_texts, focus_sentence_count = _build_focus_texts(texts, params["focus_keywords"])

    words, counts = extract_wordcloud_words(
        focus_texts,
        topk=params["topk"],
        min_len=params["min_len"],
        max_len=params["max_len"],
        allowed_pos=params["allowed_pos"],
        remove_substrings=params["remove_substrings"],
    )

    full_text = "\n".join(texts)
    unique_words = len(counts)
    total_tokens = int(sum(counts.values()))
    tail_count1 = sum(1 for item in words if item.get("count") == 1)
    tail_count1_ratio = round(tail_count1 / len(words), 4) if words else 0.0
    max_count = max((item.get("count", 0) for item in words), default=0)
    min_count = min((item.get("count", 0) for item in words), default=0)

    pos_options = [
        {"pos": "n", "label": "名词"},
        {"pos": "nt", "label": "机构团体"},
        {"pos": "nz", "label": "其他专名"},
        {"pos": "vn", "label": "名动词"},
        {"pos": "v", "label": "动词"},
        {"pos": "eng", "label": "英文"},
    ]

    evidence_enabled = bool(params["with_evidence"])
    evidence_map = {}
    evidence_coverage = 0.0
    if evidence_enabled:
        evidence_map = build_evidence_map(texts, words, params["evidence_topn"])
        evidence_coverage = round(len(evidence_map) / len(words), 4) if words else 0.0

    response_data = {
        "topk": params["topk"],
        "min_len": params["min_len"],
        "max_len": params["max_len"],
        "word_count": len(words),
        "words": words,
        "meta": {
            "language": guess_language(full_text),
            "input_type": "text" if len(texts) == 1 else "texts",
            "input_count": len(texts),
            "input_chars": int(sum(len(item) for item in texts)),
            "focus_theme": params["focus_theme"],
            "focus_keywords": params["focus_keywords"],
            "focus_sentence_count": int(focus_sentence_count),
            "allowed_pos": params["allowed_pos"],
            "remove_substrings": params["remove_substrings"],
            "unique_words": int(unique_words),
            "total_tokens": int(total_tokens),
            "tail_count1_ratio": float(tail_count1_ratio),
            "evidence_enabled": evidence_enabled,
            "evidence_topn_effective": int(params["evidence_topn"]),
            "evidence_coverage": float(evidence_coverage),
        },
        "stats": {
            "max_count": int(max_count),
            "min_count": int(min_count),
        },
        "limits": {
            "topk": {"min": 10, "max": 200},
            "min_len": {"min": 1, "max": 6},
            "max_len": {"min": 2, "max": 10},
        },
        "pos_options": pos_options,
    }

    if evidence_enabled:
        response_data["evidence_map"] = evidence_map
    return response_data


def _extract_keywords(text, sentences, kw_min=3, kw_max=5):
    vectorizer, tfidf = _build_tfidf(sentences, min_len=3, max_len=5)
    keywords, score_map = _rank_terms(vectorizer, tfidf)
    keywords = _dedupe_substrings(keywords)

    if len(keywords) < kw_min:
        vectorizer, tfidf = _build_tfidf(sentences, min_len=2, max_len=5)
        keywords_2, score_map_2 = _rank_terms(vectorizer, tfidf)
        keywords_2 = _dedupe_substrings(keywords_2)
        for keyword in keywords_2:
            if keyword not in keywords:
                keywords.append(keyword)
                score_map[keyword] = score_map_2.get(keyword, 0.0)

    if not keywords:
        tokens = _tokenize(text, min_len=3, max_len=5)
        if not tokens:
            return [], [], None, None
        counts = Counter(tokens)
        pairs = counts.most_common(kw_max)
        words = _dedupe_substrings([item for item, _ in pairs])
        words = words[:kw_max]
        return words, [float(counts[word]) for word in words], None, None

    size = min(kw_max, len(keywords))
    if size < kw_min:
        size = len(keywords)
    selected = keywords[:size]
    scores = [float(score_map.get(keyword, 0.0)) for keyword in selected]

    if len(selected) < kw_min:
        tokens = _tokenize(text, min_len=2, max_len=5)
        counts = Counter(tokens)
        for keyword, _ in counts.most_common():
            if keyword not in selected:
                selected.append(keyword)
                scores.append(float(score_map.get(keyword, counts[keyword])))
            if len(selected) >= kw_min:
                break

    return selected, scores, vectorizer, tfidf


def _score_sentences(sentences, vectorizer, tfidf, keywords):
    if not sentences:
        return []
    if vectorizer is None or tfidf is None or not keywords:
        return [sum(sentence.count(keyword) for keyword in keywords) for sentence in sentences]

    vocab = vectorizer.vocabulary_
    indices = [vocab[keyword] for keyword in keywords if keyword in vocab]
    scores = []
    for idx in range(len(sentences)):
        row = tfidf[idx]
        if indices:
            scores.append(float(row[0, indices].sum()))
        else:
            scores.append(float(row.sum()))
    return scores


def _pick_sentence_source(sentences, scores, keyword):
    best_idx = None
    best_score = -1.0
    for idx, sentence in enumerate(sentences):
        if keyword in sentence and scores[idx] > best_score:
            best_score = scores[idx]
            best_idx = idx

    if best_idx is None:
        best_idx = scores.index(max(scores)) if scores else 0

    evidence = sentences[best_idx] if sentences else ""
    if len(evidence) > 30:
        evidence = evidence[:30] + "..."
    return best_idx + 1, evidence


def _build_summary(text, sentences, scores, max_sentences=3):
    if not sentences:
        return text[:100] + ("..." if len(text) > 100 else "")

    idx_scores = list(enumerate(scores))
    idx_scores.sort(key=lambda item: item[1], reverse=True)
    top_n = max(1, min(int(max_sentences), len(sentences)))
    selected_idx = sorted([idx for idx, _ in idx_scores[:top_n]])
    summary = "".join(sentences[idx] for idx in selected_idx)

    if len(summary) < 50:
        for idx in range(len(sentences)):
            if idx in selected_idx:
                continue
            summary += sentences[idx]
            if len(summary) >= 50:
                break

    if len(summary) > 100:
        summary = summary[:100] + "..."
    return summary


def _unique_keep_order(items: Iterable):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _fallback_terms_from_texts(texts, topn):
    counter = Counter()
    for text in texts:
        if not text:
            continue

        tokens = _tokenize(text, min_len=2, max_len=6)
        for token in tokens:
            token = str(token).strip()
            if token:
                counter[token] += 1

        for token in re.findall(r"[\u4e00-\u9fff]{2,6}", text):
            token = token.strip()
            if token and token not in CN_STOP:
                counter[token] += 1

        for token in re.findall(r"[A-Za-z]{3,}", text):
            token = token.lower().strip()
            if token:
                counter[token] += 1

    if counter:
        return [word for word, _ in counter.most_common(max(1, int(topn)))]

    joined = re.sub(r"\s+", "", "".join([str(text) for text in texts if text]))
    if joined:
        return [joined[: min(8, len(joined))]]
    return []


def _pick_theme_evidence(sentences, theme, cue_words=None):
    if not sentences:
        return ""

    cue_words = cue_words or []
    best_idx = 0
    best_score = -1.0
    for idx, sentence in enumerate(sentences):
        hit_words = sum(1 for word in cue_words if word and word in sentence)
        hit_count = sum(sentence.count(word) for word in cue_words if word)
        score = float(hit_words * 2 + hit_count)
        if theme and theme in sentence:
            score += 2.0
        if score > best_score:
            best_score = score
            best_idx = idx

    best = sentences[best_idx] if sentences else ""
    if len(best) > 30:
        best = best[:30] + "..."
    return best


def _generate_core_theme(keywords, text):
    clean_keywords = []
    for keyword in keywords:
        cleaned = re.sub(r"[\s\u3000]+", "", str(keyword or "")).strip()
        if cleaned:
            clean_keywords.append(cleaned)
    clean_keywords = _unique_keep_order(clean_keywords)

    if not clean_keywords:
        clean_keywords = _fallback_terms_from_texts([text], topn=2)

    kw1 = clean_keywords[0] if clean_keywords else ""
    kw2 = clean_keywords[1] if len(clean_keywords) > 1 else ""

    if kw1 and kw2:
        pair = f"{kw1}{kw2}"
        if 2 <= len(pair) <= 12:
            return pair

    if kw1:
        for suffix in GENERIC_THEME_SUFFIXES:
            candidate = f"{kw1}{suffix}"
            if 2 <= len(candidate) <= 12:
                return candidate
        return kw1[:12]

    stripped = re.sub(r"[\s\W_]+", "", str(text or ""))
    return stripped[:8] if stripped else ""


def _build_extract_vectorizer(sentences):
    vectorizer_candidates = [
        TfidfVectorizer(
            tokenizer=lambda item: _tokenize(item, min_len=2, max_len=5),
            lowercase=False,
        ),
        TfidfVectorizer(
            analyzer="char",
            ngram_range=(1, 3),
            lowercase=False,
        ),
    ]

    for vectorizer in vectorizer_candidates:
        try:
            tfidf = vectorizer.fit_transform(sentences)
            if tfidf is not None and tfidf.shape[1] > 0:
                return vectorizer, tfidf
        except Exception:
            continue
    return None, None


def _pick_keyword_source(sentences, sentence_scores, keyword, with_evidence):
    if not with_evidence:
        return {"sentence_index": 0, "evidence": ""}
    if not keyword or not sentences:
        return {"sentence_index": 0, "evidence": ""}
    sentence_index, evidence = _pick_sentence_source(sentences, sentence_scores, keyword)
    return {"sentence_index": int(sentence_index), "evidence": evidence}


def _build_extract_topics(doc_items, params):
    topic_k = params["topic_k"]
    topn_keywords = params["topn_keywords"]
    with_evidence = params["with_evidence"]
    evidence_topn = params["evidence_topn"]
    normalize_score = params["normalize_score"]

    file_count = len(doc_items)
    topics = []
    topic_keyword_weights = []
    doc_topic_scores = np.zeros((file_count, topic_k), dtype=float)

    if file_count <= 0 or topic_k <= 0:
        return topics, topic_keyword_weights, doc_topic_scores, 0, 0

    all_sentences = []
    for item in doc_items:
        doc_sentences = [sentence for sentence in item["sentences"] if str(sentence).strip()]
        if not doc_sentences:
            doc_sentences = [item["text"]]
        for sentence in doc_sentences:
            all_sentences.append(sentence)

    if not all_sentences:
        all_sentences = [item["text"] for item in doc_items]

    vectorizer, sentence_tfidf = _build_extract_vectorizer(all_sentences)
    if vectorizer is None or sentence_tfidf is None:
        return topics, topic_keyword_weights, doc_topic_scores, 0, len(all_sentences)

    term_list = list(vectorizer.get_feature_names_out())
    if not term_list:
        return topics, topic_keyword_weights, doc_topic_scores, 0, len(all_sentences)

    sentence_dense = np.asarray(sentence_tfidf.todense())
    if sentence_dense.ndim == 1:
        sentence_dense = sentence_dense.reshape(1, -1)

    if sentence_dense.shape[0] < topic_k:
        repeat_times = int(np.ceil(topic_k / max(sentence_dense.shape[0], 1)))
        modeling_dense = np.vstack([sentence_dense for _ in range(repeat_times)])[:topic_k]
    else:
        modeling_dense = sentence_dense

    kmeans = KMeans(n_clusters=topic_k, random_state=42, n_init=20)
    kmeans.fit(modeling_dense)
    centers = np.asarray(kmeans.cluster_centers_)
    sentence_labels = kmeans.predict(sentence_dense)

    global_scores = sentence_tfidf.sum(axis=0).A1
    global_order = np.argsort(global_scores)[::-1]
    all_texts_for_fallback = [item["text"] for item in doc_items]

    selected_centers = []
    seen_theme = set()

    for topic_idx in range(topic_k):
        center_vec = centers[topic_idx]
        order = np.argsort(center_vec)[::-1]
        keyword_pairs = []
        used_terms = set()

        for term_idx in order:
            if len(keyword_pairs) >= topn_keywords:
                break
            if term_idx >= len(term_list):
                continue
            term = str(term_list[term_idx]).strip()
            if not term or term in used_terms:
                continue
            weight = float(center_vec[term_idx])
            if weight <= 0:
                continue
            keyword_pairs.append((term, weight))
            used_terms.add(term)

        for term_idx in global_order:
            if len(keyword_pairs) >= topn_keywords:
                break
            if term_idx >= len(term_list):
                continue
            term = str(term_list[term_idx]).strip()
            if not term or term in used_terms:
                continue
            weight = float(center_vec[term_idx]) if term_idx < len(center_vec) else 0.0
            if weight <= 0:
                weight = float(global_scores[term_idx])
            keyword_pairs.append((term, weight))
            used_terms.add(term)

        if len(keyword_pairs) < topn_keywords:
            fallback_terms = _fallback_terms_from_texts(all_texts_for_fallback, topn_keywords)
            for rank, term in enumerate(fallback_terms):
                if len(keyword_pairs) >= topn_keywords:
                    break
                if term in used_terms:
                    continue
                keyword_pairs.append((term, float(max(1, topn_keywords - rank))))
                used_terms.add(term)

        topic_keywords = [keyword for keyword, _ in keyword_pairs[:topn_keywords]]
        topic_keywords = _unique_keep_order(topic_keywords)
        max_weight = max((weight for _, weight in keyword_pairs), default=1.0)
        if max_weight <= 0:
            max_weight = 1.0

        topic_sentence_ids = np.where(sentence_labels == topic_idx)[0].tolist()
        topic_sentences = [all_sentences[idx] for idx in topic_sentence_ids if idx < len(all_sentences)]
        topic_text = "\n".join(topic_sentences) if topic_sentences else " ".join(topic_keywords)
        topic_theme = _generate_core_theme(topic_keywords, topic_text)
        if not topic_theme or topic_theme in seen_theme:
            continue
        seen_theme.add(topic_theme)

        topic_weight_map = {}
        topic_keyword_details = []
        source_sentences = topic_sentences[:evidence_topn] if topic_sentences else all_sentences[:evidence_topn]
        topic_sentence_scores = _score_sentences(source_sentences, None, None, topic_keywords)

        for keyword, raw_weight in keyword_pairs[:topn_keywords]:
            norm_weight = round(float(raw_weight / max_weight), 4)
            topic_weight_map[keyword] = norm_weight
            source = _pick_keyword_source(source_sentences, topic_sentence_scores, keyword, with_evidence)
            topic_keyword_details.append(
                {
                    "text": keyword,
                    "weight": float(norm_weight),
                    "count": int(sum(item["text"].count(keyword) for item in doc_items)),
                    "source": source,
                }
            )

        real_topic_index = len(topics) + 1
        topics.append(
            {
                "topic_index": real_topic_index,
                "theme": topic_theme,
                "keywords": [item["text"] for item in topic_keyword_details],
                "keyword_details": topic_keyword_details,
            }
        )
        topic_keyword_weights.append(topic_weight_map)
        selected_centers.append(center_vec)

    if selected_centers:
        doc_tfidf = vectorizer.transform([item["text"] for item in doc_items])
        valid_centers = np.asarray(selected_centers)
        raw_scores = np.asarray(doc_tfidf.dot(valid_centers.T))
        raw_scores = np.maximum(raw_scores, 0.0)
    else:
        raw_scores = np.zeros((file_count, 0), dtype=float)

    if normalize_score and raw_scores.size > 0:
        row_max = raw_scores.max(axis=1, keepdims=True)
        row_max[row_max <= 0] = 1.0
        doc_topic_scores = raw_scores / row_max
    else:
        doc_topic_scores = raw_scores

    return topics, topic_keyword_weights, doc_topic_scores, len(topics), len(all_sentences)


def _build_visualization_payload(files, topics, score_matrix):
    topic_items = []
    for idx, topic in enumerate(topics or []):
        topic_index = int(topic.get("topic_index", idx + 1))
        topic_items.append(
            {
                "topic_index": topic_index,
                "theme": topic.get("theme", f"主题{topic_index}"),
                "keywords": topic.get("keywords", []),
            }
        )

    doc_items = []
    for idx, item in enumerate(files or []):
        file_index = int(item.get("index", idx + 1))
        doc_items.append(
            {
                "id": item.get("id", f"doc{file_index}"),
                "index": file_index,
                "name": item.get("name", f"文档{file_index}"),
            }
        )

    topic_count = len(topic_items)
    file_count = len(doc_items)
    matrix_values = []
    matrix_data = []
    relation_series = []
    max_score = 0.0

    for doc_idx in range(file_count):
        doc = doc_items[doc_idx]
        row = []
        for topic_idx in range(topic_count):
            score = 0.0
            if isinstance(score_matrix, np.ndarray) and score_matrix.ndim >= 2:
                if doc_idx < score_matrix.shape[0] and topic_idx < score_matrix.shape[1]:
                    score = float(score_matrix[doc_idx, topic_idx])
            row.append(score)
            matrix_data.append([topic_idx, doc_idx, score])
            max_score = max(max_score, score)
        matrix_values.append(row)
        relation_series.append({"doc_id": doc["id"], "name": doc["name"], "data": row})

    x_axis = [
        {
            "topic_index": item["topic_index"],
            "theme": item["theme"],
            "label": f"T{item['topic_index']} {item['theme']}",
        }
        for item in topic_items
    ]
    y_axis = [
        {"file_id": item["id"], "file_index": item["index"], "name": item["name"]}
        for item in doc_items
    ]

    return {
        "matrix": {
            "x": [item["topic_index"] for item in topic_items],
            "y": [item["id"] for item in doc_items],
            "topic_labels": [item["label"] for item in x_axis],
            "doc_labels": [item["name"] for item in doc_items],
            "topic_items": topic_items,
            "doc_items": doc_items,
            "values": matrix_values,
            "data": matrix_data,
            "max": float(max_score),
        },
        "relation": {"x_axis": x_axis, "series": relation_series},
        "heatmap": {
            "x_axis": x_axis,
            "y_axis": y_axis,
            "data": matrix_data,
            "max": float(max_score),
        },
    }


def build_extract_result(texts, file_names, params):
    """构建 /extract 的核心结果，接口层只负责调用和封装。"""
    all_files = []
    doc_items = []
    upload_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    for idx, raw_text in enumerate(texts):
        if raw_text is None or not str(raw_text).strip():
            continue

        text = str(raw_text)
        file_index = idx + 1
        file_id = f"doc{file_index}"
        sentences = [sentence for sentence in split_sentences(text) if str(sentence).strip()]
        if not sentences:
            sentences = [text]

        request_file_name = file_names[idx] if idx < len(file_names) else ""
        file_info = {
            "id": file_id,
            "name": request_file_name or f"文档{file_index}",
            "index": file_index,
            "word_count": len(text),
            "sentence_count": len(sentences),
            "language": guess_language(text),
            "upload_time": upload_time_str,
        }
        all_files.append(file_info)
        doc_items.append(
            {
                "file_id": file_id,
                "file_index": file_index,
                "file_name": file_info["name"],
                "text": text,
                "sentences": sentences,
            }
        )

    global_topics, _, global_doc_topic_scores, global_modeled_topic_k, global_unit_count = _build_extract_topics(
        doc_items, params
    )

    topics = []
    topic_name_to_index = {}
    doc_theme_map = {}
    unit_count = 0
    modeled_topic_k = 0

    for item in doc_items:
        local_topics, local_weight_maps, local_score_matrix, local_modeled_topic_k, local_unit_count = _build_extract_topics(
            [item], params
        )
        unit_count += int(local_unit_count)
        modeled_topic_k = max(int(modeled_topic_k), int(local_modeled_topic_k))
        sentences = item["sentences"]

        for local_idx, local_topic in enumerate(local_topics):
            raw_keywords = _unique_keep_order(local_topic.get("keywords") or [])
            if not raw_keywords:
                raw_keywords = _fallback_terms_from_texts([item["text"]], params["topn_keywords"])

            matched_pairs = []
            selected_set = set()
            for keyword in raw_keywords:
                keyword_count = int(item["text"].count(keyword))
                if keyword_count > 0 and keyword not in selected_set:
                    matched_pairs.append((keyword, keyword_count))
                    selected_set.add(keyword)
                if len(matched_pairs) >= params["topn_keywords"]:
                    break

            if len(matched_pairs) < 2:
                continue

            keywords = [keyword for keyword, _ in matched_pairs]
            doc_theme_name = _generate_core_theme(keywords, item["text"])
            if not doc_theme_name:
                doc_theme_name = local_topic.get("theme") or ""
            if not doc_theme_name:
                continue

            if doc_theme_name not in topic_name_to_index:
                topic_index = len(topics) + 1
                topic_name_to_index[doc_theme_name] = topic_index
                local_weight_map = local_weight_maps[local_idx] if local_idx < len(local_weight_maps) else {}
                topic_keyword_details = []
                for keyword, keyword_count in matched_pairs:
                    topic_keyword_details.append(
                        {
                            "text": keyword,
                            "weight": float(local_weight_map.get(keyword, 0.0)),
                            "count": int(keyword_count),
                            "source": {"sentence_index": 0, "evidence": ""},
                        }
                    )
                topics.append(
                    {
                        "topic_index": topic_index,
                        "theme": doc_theme_name,
                        "keywords": keywords,
                        "keyword_details": topic_keyword_details,
                    }
                )

            topic_index = topic_name_to_index[doc_theme_name]
            sentence_scores = _score_sentences(sentences, None, None, keywords)
            summary_max_sentences = 2 if params["granularity"] == "doc" else 3
            summary = _build_summary(item["text"], sentences, sentence_scores, max_sentences=summary_max_sentences)
            theme_evidence = ""
            if params["with_evidence"]:
                theme_evidence = _pick_theme_evidence(
                    sentences[: params["evidence_topn"]],
                    doc_theme_name,
                    cue_words=keywords[: params["topn_keywords"]],
                )

            keyword_details = []
            weight_map = local_weight_maps[local_idx] if local_idx < len(local_weight_maps) else {}
            for keyword, keyword_count in matched_pairs:
                source = _pick_keyword_source(
                    sentences[: params["evidence_topn"]],
                    sentence_scores[: params["evidence_topn"]],
                    keyword,
                    params["with_evidence"],
                )
                keyword_details.append(
                    {
                        "text": keyword,
                        "weight": float(weight_map.get(keyword, 0.0)),
                        "count": int(keyword_count),
                        "source": source,
                    }
                )

            score_value = float(local_score_matrix[0, local_idx]) if local_score_matrix.size else 0.0
            record_key = (item["file_id"], int(topic_index))
            new_record = {
                "theme": doc_theme_name,
                "keywords": [detail["text"] for detail in keyword_details],
                "file_index": item["file_index"],
                "id": f"{item['file_id']}-t{topic_index}",
                "summary": summary,
                "keyword_details": keyword_details,
                "theme_evidence": theme_evidence,
                "confidence": round(min(1.0, max(0.0, score_value)), 4),
                "topic_index": int(topic_index),
                "file_id": item["file_id"],
                "score": score_value,
            }
            old_record = doc_theme_map.get(record_key)
            if old_record is None or float(new_record["score"]) > float(old_record.get("score", 0.0)):
                doc_theme_map[record_key] = new_record

    doc_themes = sorted(
        list(doc_theme_map.values()),
        key=lambda item: (int(item.get("file_index", 0)), int(item.get("topic_index", 0))),
    )
    effective_k = len(topics)
    score_matrix = np.zeros((len(doc_items), effective_k), dtype=float)
    for item in doc_themes:
        y_idx = int(item["file_index"]) - 1
        x_idx = int(item["topic_index"]) - 1
        if 0 <= y_idx < score_matrix.shape[0] and 0 <= x_idx < score_matrix.shape[1]:
            score_matrix[y_idx, x_idx] = max(float(score_matrix[y_idx, x_idx]), float(item.get("score", 0.0)))

    visual_topics = global_topics if global_topics else topics
    visual_score_matrix = global_doc_topic_scores if len(global_topics) else score_matrix
    visual_payload = _build_visualization_payload(all_files, visual_topics, visual_score_matrix)

    total_theme_count = int(len(visual_topics))
    total_doc_theme_count = int(len(doc_themes))

    return {
        "files": all_files,
        "doc_items": doc_items,
        "doc_themes": doc_themes,
        "topics": visual_topics,
        "matrix": visual_payload["matrix"],
        "relation": visual_payload["relation"],
        "heatmap": visual_payload["heatmap"],
        "statistics": {
            "file_count": len(all_files),
            "theme_count": total_theme_count,
            "doc_theme_count": total_doc_theme_count,
            "global_topic_count": int(len(global_topics)),
            "algorithm_version": "http-v1.1.0",
        },
        "debug": {
            "modeled_topic_k": int(max(modeled_topic_k, global_modeled_topic_k)),
            "unit_count": int(max(unit_count, global_unit_count)),
        },
    }


def extract_open_theme_from_text(text: str, kw_min: int = 3, kw_max: int = 5) -> dict:
    """兼容旧接口：从单篇文本中提取一个主题。"""
    text = str(text or "").strip()
    if not text:
        return {"theme": "", "keywords": [], "summary": "", "keyword_details": []}

    sentences = split_sentences(text)
    if not sentences:
        sentences = [text]

    keywords, _, vectorizer, tfidf = _extract_keywords(text, sentences, kw_min=kw_min, kw_max=kw_max)
    keywords = _unique_keep_order(keywords)[:kw_max]
    sentence_scores = _score_sentences(sentences, vectorizer, tfidf, keywords)
    summary = _build_summary(text, sentences, sentence_scores, max_sentences=3)
    theme = _generate_core_theme(keywords, text)

    keyword_details = []
    for keyword in keywords:
        sentence_index, evidence = _pick_sentence_source(sentences, sentence_scores, keyword)
        keyword_details.append(
            {
                "text": keyword,
                "weight": float(round(text.count(keyword) / max(len(text), 1), 4)),
                "count": int(text.count(keyword)),
                "source": {"sentence_index": int(sentence_index), "evidence": evidence},
            }
        )

    return {
        "theme": theme,
        "keywords": keywords,
        "summary": summary,
        "keyword_details": keyword_details,
    }


def extract_themes_from_text(texts, kw_min: int = 3, kw_max: int = 5) -> list[dict]:
    """兼容旧接口：支持单文本或多文本输入。"""
    if isinstance(texts, str):
        source_texts = [texts]
    elif isinstance(texts, Iterable):
        source_texts = [str(text) for text in texts if str(text or "").strip()]
    else:
        source_texts = []

    result = []
    for index, text in enumerate(source_texts, start=1):
        theme_info = extract_open_theme_from_text(text, kw_min=kw_min, kw_max=kw_max)
        theme_info["file_index"] = index
        result.append(theme_info)
    return result


if __name__ == "__main__":
    logger.info("app_logic.py 是算法模块，不提供 HTTP 服务。请运行 python api_adapter.py 启动项目。")
