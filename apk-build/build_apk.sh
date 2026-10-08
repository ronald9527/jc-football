#!/usr/bin/env bash
# 竞彩足球数据分析台 - APK 手动构建（aapt2 + d8 + zipalign + apksigner）
# 不依赖 Gradle/Android Gradle Plugin，避免版本兼容问题
set -euo pipefail

ANDROID_HOME=/opt/android-sdk
BT="$ANDROID_HOME/build-tools/33.0.2"
AJ="$ANDROID_HOME/platforms/android-33/android.jar"
PROJ=/workspace/apk-build

cd "$PROJ"

echo "[0/7] 同步版本号（网页版本为唯一真相源，APK 必须跟随）"
# 防止「网页 v1.2.2 / App v1.5」这种分叉：每次打包前强制以网页版本为准回写 manifest
if [ -f /workspace/jc-football/version_sync.py ]; then
  python3 /workspace/jc-football/version_sync.py --apply
else
  echo "! 未找到 version_sync.py，沿用 AndroidManifest 现有版本号"
fi

# 输出文件名自动跟随 AndroidManifest 的 versionName，避免版本号与文件名不一致
VNAME=$(sed -n 's/.*android:versionName="\([^"]*\)".*/\1/p' AndroidManifest.xml | head -1)
VCODE=$(sed -n 's/.*android:versionCode="\([^"]*\)".*/\1/p' AndroidManifest.xml | head -1)
OUT=/workspace/竞彩足球数据分析台-v${VNAME}.apk
rm -rf build gen obj dex
mkdir -p build gen obj dex assets

echo "[1/7] 生成内置快照（完整历史 + 今日最新数据 + 去豆包化）"
python3 prepare_snapshot.py
cp /workspace/jc-football/matches.json assets/matches.json
ls -lh assets/index.html assets/matches.json

echo "[2/7] aapt2 编译资源"
"$BT/aapt2" compile --dir res -o build/res.zip

echo "[3/7] aapt2 链接（生成未签名 APK + R.java）"
"$BT/aapt2" link -I "$AJ" \
  --manifest AndroidManifest.xml \
  -o build/app-unaligned.apk \
  --java gen \
  --min-sdk-version 21 --target-sdk-version 33 \
  --auto-add-overlay \
  build/res.zip -A assets

echo "[4/7] javac 编译 Java 源码"
find gen src -name '*.java' > build/sources.txt
# 注意：不要用 `|| true` 吞掉编译失败——否则 javac 报错时仍会继续打包，
# 产物里的 classes.dex 会缺少 MainActivity（表现为 App 打开白屏）。
set +e
javac -nowarn -encoding UTF-8 -source 8 -target 8 \
  -bootclasspath "$AJ" -classpath "$AJ" \
  -d obj @build/sources.txt 2>&1 | grep -v 'warning'
JAVAC_RC=${PIPESTATUS[0]}
set -e
if [ "$JAVAC_RC" -ne 0 ]; then
  echo "❌ javac 编译失败（exit=$JAVAC_RC），已终止构建"
  exit 1
fi
if [ ! -f obj/com/jcfootball/panel/MainActivity.class ]; then
  echo "❌ 未生成 MainActivity.class，已终止构建"
  exit 1
fi

echo "[5/7] d8 转 DEX"
"$BT/d8" --lib "$AJ" --min-api 21 \
  --output dex $(find obj -name '*.class')
ls -lh dex/classes.dex

echo "[6/7] 注入 DEX + zipalign"
python3 - <<'PY'
import zipfile, os
apk = os.path.join("build", "app-unaligned.apk")
if os.path.exists(os.path.join("dex", "classes.dex")):
    with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join("dex", "classes.dex"), "classes.dex")
    print("  classes.dex 已注入")
else:
    raise SystemExit("classes.dex 未生成")
PY
"$BT/zipalign" -f -p 4 build/app-unaligned.apk build/app-aligned.apk

echo "[7/7] 签名"
KS=jcfootball.keystore
if [ ! -f "$KS" ]; then
  keytool -genkeypair -v -keystore "$KS" -alias jcfootball \
    -keyalg RSA -keysize 2048 -validity 10950 \
    -storepass jcfootball2026 -keypass jcfootball2026 \
    -dname "CN=JC Football, OU=App, O=JC, L=Beijing, S=Beijing, C=CN"
fi
rm -f "$OUT"
"$BT/apksigner" sign --ks "$KS" --ks-key-alias jcfootball \
  --ks-pass pass:jcfootball2026 --key-pass pass:jcfootball2026 \
  --out "$OUT" build/app-aligned.apk

"$BT/apksigner" verify --print-certs "$OUT" | head -8
ls -lh "$OUT"
echo "构建完成: $OUT"
echo "版本: versionName=$VNAME versionCode=$VCODE（与网页版本一致）"
