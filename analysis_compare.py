"""跨任务主题与关键词对比模块。"""


def _theme_names(snapshot: dict) -> set[str]:
    return {
        str(item.get("theme", "") or "").strip()
        for item in snapshot.get("themes", [])
        if str(item.get("theme", "") or "").strip()
    }


def _keywords(snapshot: dict) -> set[str]:
    words = set()
    for item in snapshot.get("themes", []):
        words.update(str(word or "").strip() for word in item.get("keywords", []) if str(word or "").strip())
    return words


def compare_task_snapshots(snapshots: list[dict]) -> dict:
    """对 2~5 个任务快照计算共同主题、独有主题和关键词重合度。"""
    if len(snapshots) < 2:
        raise ValueError("至少选择两个任务进行对比")

    theme_sets = [_theme_names(snapshot) for snapshot in snapshots]
    common_themes = set.intersection(*theme_sets) if theme_sets else set()
    all_keywords = [_keywords(snapshot) for snapshot in snapshots]
    keyword_intersection = set.intersection(*all_keywords) if all_keywords else set()
    keyword_union = set.union(*all_keywords) if all_keywords else set()

    tasks = []
    for index, snapshot in enumerate(snapshots):
        other_themes = set().union(*(theme_sets[:index] + theme_sets[index + 1 :]))
        tasks.append({
            "task_id": int(snapshot.get("task_id", 0) or 0),
            "name": str(snapshot.get("name", "") or ""),
            "algorithm_version": str(snapshot.get("algorithm_version", "") or ""),
            "theme_count": len(theme_sets[index]),
            "keyword_count": len(all_keywords[index]),
            "unique_themes": sorted(theme_sets[index] - other_themes),
            "themes": snapshot.get("themes", []),
            "quality_metrics": snapshot.get("quality_metrics", {}),
        })

    return {
        "tasks": tasks,
        "common_themes": sorted(common_themes),
        "common_keywords": sorted(keyword_intersection),
        "keyword_jaccard": round(
            len(keyword_intersection) / len(keyword_union) if keyword_union else 0.0,
            4,
        ),
    }
