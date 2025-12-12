from typing import List, Dict
import re
import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

# ---- 停用词（可逐步扩充）----
CN_STOP = set("""
的 了 和 与 及 或 就 在 是 为 对 于 并 而 把 被 有 无 中 上 下 其中 以及 通过 关于 由于
我们 你们 他们 它们 本文 以上 以下 以及其 等 等等 每个 每年 每日 每月
项目 工作 方案 情况 数据 分析 研究 系统 模型 方法 结果 影响 问题 解决 进行 提供 采用
""".split())
EN_STOP = set("""
a an the and or for to of in on with without by from as at into about over under above
this that these those it its we you they them our your their is are was were be been being
have has had do does did doing can could should would may might will
""".split())

SPLIT_PTN = re.compile(r"[。！？!?；;.\n]+")
WORD_PTN = re.compile(r"[A-Za-z0-9_]+")

def split_sentences(text: str) -> List[str]:
    sents = [s.strip() for s in SPLIT_PTN.split(text) if s.strip()]
    return sents if sents else [text.strip()]

def tokenize(sentence: str) -> List[str]:
    # 粗判中文比例
    chinese_ratio = sum('\u4e00' <= ch <= '\u9fff' for ch in sentence) / max(len(sentence), 1)
    if chinese_ratio > 0.3:
        tokens = [w.strip() for w in jieba.lcut(sentence) if w.strip()]
        tokens = [w for w in tokens if w not in CN_STOP and len(w) > 1]
    else:
        tokens = [w.lower() for w in WORD_PTN.findall(sentence)]
        tokens = [w for w in tokens if w not in EN_STOP and len(w) > 1]
    return tokens

def _analyzer(doc: str):
    return tokenize(doc)

def extract_themes(sentences: List[str], n_themes: int = 3, topk: int = 3) -> List[Dict]:
    # 样本过少时填充
    if len(sentences) < n_themes:
        sentences = sentences + [sentences[-1]] * (n_themes - len(sentences))

    vectorizer = TfidfVectorizer(analyzer=_analyzer, max_features=5000)
    X = vectorizer.fit_transform(sentences)

    # 极端兜底：全是停用词或稀疏失效
    if X.shape[1] == 0:
        return [{"theme": "通用主题", "keywords": ["兴趣", "关注", "领域"]} for _ in range(n_themes)]

    # 使用固定 n_init=10，避免不同 sklearn 版本对 "auto" 的兼容差异
    km = KMeans(n_clusters=n_themes, n_init=10, random_state=42)
    labels = km.fit_predict(X)

    terms = vectorizer.get_feature_names_out()
    centers = km.cluster_centers_  # [k, vocab]

    themes = []
    used_theme_words = set()

    for i in range(n_themes):
        weights = centers[i]
        idxs = weights.argsort()[::-1]

        # 主题名：从前 20 个挑一个未用过的
        theme_word = None
        for j in idxs[:20]:
            w = terms[j]
            if w not in used_theme_words:
                theme_word = w
                break
        if theme_word is None:
            theme_word = terms[idxs[0]]
        used_theme_words.add(theme_word)

        # 关键词：从高到低取 topk，跳过主题名本身
        kws = []
        for j in idxs:
            w = terms[j]
            if w == theme_word:
                continue
            if w not in kws:
                kws.append(w)
            if len(kws) >= topk:
                break
        while len(kws) < topk:
            kws.append(theme_word)

        themes.append({"theme": theme_word, "keywords": kws})

    return themes

def extract_themes_from_text(text: str, n_themes: int = 3, topk: int = None) -> List[Dict]:
    text = (text or "").strip()
    if not text:
        return [{"theme": "通用主题", "keywords": ["兴趣", "关注", "领域"]} for _ in range(n_themes)]

    # -------- 新增：根据文本自动决定关键词数量 --------
    length = len(text)

    if topk is None:  # 允许调用者传入自定义 topk，否则自动适配
        if length < 2000:
            topk = 5
        elif length < 4000:
            topk = 8
        elif length < 8000:
            topk = 12
        else:
            topk = 16

        # 强制限制区间
        topk = max(5, min(topk, 20))

    # -------- 句子切分 --------
    sents = split_sentences(text)
    return extract_themes(sents, n_themes=n_themes, topk=topk)
