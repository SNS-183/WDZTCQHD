-- 只读审计历史无归属批次；确认归属前不得自动绑定到任何用户。
SELECT
    at.task_id,
    at.task_name,
    at.task_status,
    at.file_count,
    at.create_time,
    COUNT(di.document_id) AS actual_document_count
FROM analysis_tasks AS at
LEFT JOIN document_info AS di ON di.task_id = at.task_id
WHERE at.user_id IS NULL
GROUP BY at.task_id, at.task_name, at.task_status, at.file_count, at.create_time
ORDER BY at.create_time DESC, at.task_id DESC;
