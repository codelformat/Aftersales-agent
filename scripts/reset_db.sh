#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose down -v
# MySQL 重建后会话 ID 从 1 开始。删除 checkpoint，避免新会话读到旧会话的 State。
rm -f data/checkpoints.sqlite data/checkpoints.sqlite-wal data/checkpoints.sqlite-shm
docker compose up -d --wait
docker exec aftersales-mysql mysql -uaftersales -paftersales -e "SELECT COUNT(*) FROM aftersales.faq;"
faq_question_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(question) FROM faq WHERE id = 1")
# 使用 HEX 校验存储字节，避免双重编码被 latin1 客户端反向还原后漏检。
if [[ "$faq_question_hex" != "E98080E8B4A7E694BFE7AD96E698AFE4BB80E4B988EFBC9F" ]]; then
  echo "FAQ 中文编码错误"
  exit 1
fi
echo "FAQ 中文编码正确"
docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SHOW TABLES LIKE 'knowledge_chunks'" | grep -q knowledge_chunks || { echo "knowledge_chunks 未创建"; exit 1; }
for t in low_confidence_questions faith_cases conversation_summaries tool_audit_logs; do
  docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SHOW TABLES LIKE '$t'" | grep -q "$t" || { echo "$t 未创建"; exit 1; }
done
enum_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='tool_audit_logs' AND COLUMN_NAME='status'")
# 校验“权限拒绝”的 utf8mb4 字节，排除双重编码。
[[ "$enum_hex" == *E69D83E99990E68B92E7BB9D* ]] || { echo "tool_audit_logs 中文枚举编码错误"; exit 1; }
curl --fail --silent http://127.0.0.1:9091/healthz >/dev/null || { echo "Milvus 健康检查失败"; exit 1; }
echo "Milvus 健康检查正常"
