#!/bin/bash
# 生成本地「稳定代码签名证书」并导入登录钥匙串（看剧查词-美剧专用）。
#
# 为什么需要它：
#   ad-hoc 签名（codesign -s -）的「指定要求」是纯 cdhash —— 只要重新打包一次，
#   cdhash 就变，macOS 隐私设置里那条已授权的开关会变成对不上号的“僵尸记录”
#   （开关显示是开的，但 AXIsProcessTrusted() 永远返回 False，App 每次启动都
#    继续弹授权框）。
#   用一张固定的自签名证书签名后，指定要求变成
#       identifier "com.zhaowentian.subtitlelookup" and certificate leaf = H"..."
#   跟 cdhash 无关，重打包多少次权限都保持有效。
#
# ⚠️ 刻意与旧项目 doubao-lookup 区分：
#   证书名不同、中间文件用 sl.* 而不是 dl.*，
#   免得把「豆包查词」那套签名材料覆盖掉 —— 两个 App 要能共存。
#
# 用法:  bash make_cert.sh      （只需执行一次）
set -euo pipefail

CERT_NAME="SubtitleLookup Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
WORK="$HOME/.workbuddy/certs"
mkdir -p "$WORK"

if /usr/bin/security find-identity -v -p codesigning 2>/dev/null | grep -q "$CERT_NAME"; then
  echo "==> 证书「$CERT_NAME」已存在且受信任，无需重建。"
  exit 0
fi

echo "==> 1/4 生成自签名证书"
cd "$WORK"
/usr/bin/openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout sl.key -out sl.crt \
  -subj "/CN=$CERT_NAME" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning"

echo "==> 2/4 打包成 macOS 能读的 p12（必须是 SHA1/3DES 旧格式）"
/usr/bin/openssl pkcs12 -export -out sl.p12 -inkey sl.key -in sl.crt \
  -passout pass:sllocal \
  -certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1

echo "==> 3/4 导入登录钥匙串"
/usr/bin/security import sl.p12 -k "$KEYCHAIN" \
  -P sllocal -T /usr/bin/codesign -A

echo "==> 4/4 设为信任（codesign 只认「valid identity」）"
/usr/bin/security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" sl.crt

echo
/usr/bin/security find-identity -v -p codesigning | head -5
echo "完成。之后用 build.sh 打包会自动用这张证书签名。"
