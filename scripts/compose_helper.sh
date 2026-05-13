#!/usr/bin/env bash
# ============================================================
# 智护银伴 · Docker Compose 安全修改助手
# 幂等地修改 docker-compose.yml 中的 environment 变量
#
# 用法:
#   ./scripts/compose_helper.sh set-env <service> <key> <value>
#   ./scripts/compose_helper.sh validate
#   ./scripts/compose_helper.sh backup
#   ./scripts/compose_helper.sh restore
#
# 设计原则：
#   - 修改前自动备份
#   - 修改后自动验证（docker compose config）
#   - 验证失败自动回滚
#   - 已有 key 则更新，不重复插入（幂等）
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
BACKUP_FILE="$PROJECT_DIR/docker-compose.yml.bak"

# 检测 compose 命令
detect_compose_cmd() {
    if docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# 备份 compose 文件
do_backup() {
    if [ -f "$COMPOSE_FILE" ]; then
        cp "$COMPOSE_FILE" "$BACKUP_FILE"
        echo -e "${GREEN}✔${NC}  已备份: docker-compose.yml → docker-compose.yml.bak"
    else
        echo -e "${RED}✘${NC}  docker-compose.yml 不存在"
        exit 1
    fi
}

# 恢复备份
do_restore() {
    if [ -f "$BACKUP_FILE" ]; then
        cp "$BACKUP_FILE" "$COMPOSE_FILE"
        echo -e "${GREEN}✔${NC}  已从备份恢复 docker-compose.yml"
    else
        echo -e "${RED}✘${NC}  备份文件不存在: docker-compose.yml.bak"
        exit 1
    fi
}

# 验证 compose 文件语法
do_validate() {
    local compose_cmd
    compose_cmd=$(detect_compose_cmd)

    if [ -z "$compose_cmd" ]; then
        # 如果没有 docker compose，用 python yaml 解析做基础验证
        if python3 -c "
import yaml, sys
try:
    with open('$COMPOSE_FILE') as f:
        yaml.safe_load(f)
    sys.exit(0)
except Exception as e:
    print(f'YAML 解析错误: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
            echo -e "${GREEN}✔${NC}  YAML 语法验证通过"
            return 0
        else
            echo -e "${RED}✘${NC}  YAML 语法验证失败"
            return 1
        fi
    fi

    # 使用 docker compose config 做完整验证（需要 .env 存在）
    cd "$PROJECT_DIR"
    if $compose_cmd config --quiet 2>/dev/null; then
        echo -e "${GREEN}✔${NC}  docker compose config 验证通过"
        return 0
    else
        # compose config 可能因为缺 .env 变量失败，尝试更宽松的 YAML 检查
        if python3 -c "
import yaml, sys
try:
    with open('$COMPOSE_FILE') as f:
        data = yaml.safe_load(f)
    # 检查关键结构
    if 'services' not in data:
        print('缺少 services 顶级 key', file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
except Exception as e:
    print(f'YAML 解析错误: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
            echo -e "${YELLOW}⚠${NC}  YAML 结构正确（docker compose config 需要 .env 才能完全校验）"
            return 0
        else
            echo -e "${RED}✘${NC}  YAML 结构验证失败"
            return 1
        fi
    fi
}

# 幂等设置环境变量（使用 python3 操作 YAML 以避免重复 key）
do_set_env() {
    local service="$1"
    local key="$2"
    local value="$3"

    if ! python3 -c "
import yaml, sys

compose_file = '$COMPOSE_FILE'
service = '$service'
key = '$key'
value = '$value'

with open(compose_file, 'r') as f:
    content = f.read()

# 使用 yaml 解析验证结构
try:
    data = yaml.safe_load(content)
except yaml.YAMLError as e:
    print(f'YAML 解析失败: {e}', file=sys.stderr)
    sys.exit(1)

if 'services' not in data or service not in data['services']:
    print(f'服务 {service} 不存在于 docker-compose.yml', file=sys.stderr)
    sys.exit(1)

svc = data['services'][service]
if 'environment' not in svc:
    svc['environment'] = {}

# 如果 environment 是列表形式，转为字典
if isinstance(svc['environment'], list):
    env_dict = {}
    for item in svc['environment']:
        if '=' in item:
            k, v = item.split('=', 1)
            env_dict[k] = v
        else:
            env_dict[item] = ''
    svc['environment'] = env_dict

# 幂等设置
svc['environment'][key] = value

with open(compose_file, 'w') as f:
    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f'已设置 {service}.environment.{key}')
" 2>&1; then
        echo -e "${RED}✘${NC}  设置环境变量失败"
        return 1
    fi

    echo -e "${GREEN}✔${NC}  ${service}.environment.${key} = ${value}"
    return 0
}

# ── 主逻辑 ──────────────────────────────────────────────────
case "${1:-help}" in
    set-env)
        if [ $# -lt 4 ]; then
            echo "用法: $0 set-env <service> <key> <value>"
            exit 1
        fi
        do_backup
        do_set_env "$2" "$3" "$4"
        if ! do_validate; then
            echo -e "${RED}✘${NC}  验证失败，自动回滚..."
            do_restore
            exit 1
        fi
        ;;
    validate)
        do_validate
        ;;
    backup)
        do_backup
        ;;
    restore)
        do_restore
        ;;
    help|*)
        echo "智护银伴 · Docker Compose 安全修改助手"
        echo ""
        echo "用法:"
        echo "  $0 set-env <service> <key> <value>  幂等设置环境变量"
        echo "  $0 validate                          验证 compose 文件"
        echo "  $0 backup                            备份 compose 文件"
        echo "  $0 restore                           从备份恢复"
        echo ""
        echo "特性:"
        echo "  - 修改前自动备份"
        echo "  - 修改后自动验证"
        echo "  - 验证失败自动回滚"
        echo "  - 已有 key 则更新，不重复插入（幂等）"
        ;;
esac
