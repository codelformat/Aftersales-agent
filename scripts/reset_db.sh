#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose down -v
docker compose up -d --wait
docker exec aftersales-mysql mysql -uaftersales -paftersales -e "SELECT COUNT(*) FROM aftersales.faq;"
faq_question_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(question) FROM faq WHERE id = 1")
# 使用 HEX 校验存储字节，避免双重编码被 latin1 客户端反向还原后漏检。
if [[ "$faq_question_hex" != "E98080E8B4A7E694BFE7AD96E698AFE4BB80E4B988EFBC9F" ]]; then
  echo "FAQ 中文编码错误"
  exit 1
fi
echo "FAQ 中文编码正确"
