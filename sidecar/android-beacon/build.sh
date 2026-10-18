#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

JAVA_BIN="/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin"
export PATH="$JAVA_BIN:$PATH"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
SDK_DIR="/Users/var/Library/Android/sdk"
BUILD_TOOLS="$SDK_DIR/build-tools/36.0.0"
PLATFORM="$SDK_DIR/platforms/android-34/android.jar"

AAPT2="$BUILD_TOOLS/aapt2"
D8="$BUILD_TOOLS/d8"
ZIPALIGN="$BUILD_TOOLS/zipalign"
APKSIGNER="$BUILD_TOOLS/apksigner"
JAVAC="$JAVA_BIN/javac"
KEYTOOL="$JAVA_BIN/keytool"

echo "=== Building Sentinel Android GPS Beacon ==="

rm -rf build gen bin
mkdir -p build gen bin

# 1. Compile resources with aapt2
echo "[1/7] Compiling resources..."
"$AAPT2" compile --dir res -o build/compiled_res.zip

# 2. Link resources and generate R.java
echo "[2/7] Linking resources & generating R.java..."
"$AAPT2" link --manifest AndroidManifest.xml \
  -I "$PLATFORM" \
  build/compiled_res.zip \
  -o build/base.apk \
  --java gen/

# 3. Compile Java sources with javac
echo "[3/7] Compiling Java sources..."
"$JAVAC" --release 8 \
  -classpath "$PLATFORM:gen" \
  -d bin/ \
  gen/com/godseye/beacon/R.java \
  src/com/godseye/beacon/*.java

# 4. Convert bytecode to Dalvik DEX with d8
echo "[4/7] Generating classes.dex with d8..."
CLASS_FILES=$(find bin -name "*.class")
"$D8" --release --min-api 26 --output build/ $CLASS_FILES

# 5. Add classes.dex to APK
echo "[5/7] Packaging APK..."
cd build
cp base.apk unaligned.apk
zip -uj unaligned.apk classes.dex
cd ..

# 6. Align APK with zipalign
echo "[6/7] Aligning APK..."
"$ZIPALIGN" -f -p 4 build/unaligned.apk build/aligned.apk

# 7. Sign APK with apksigner
echo "[7/7] Signing APK..."
KEYSTORE="build/debug.keystore"
if [ ! -f "$KEYSTORE" ]; then
  "$KEYTOOL" -genkey -v -keystore "$KEYSTORE" \
    -alias debug -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass android -keypass android \
    -dname "CN=Sentinel,OU=Escort,O=GodsEye,C=US"
fi

"$APKSIGNER" sign --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out sentinel-gps.apk \
  build/aligned.apk

echo "=== Verifying signed APK ==="
"$APKSIGNER" verify sentinel-gps.apk

APK_SIZE=$(ls -lh sentinel-gps.apk | awk '{print $5}')
echo "SUCCESS! Output: $DIR/sentinel-gps.apk ($APK_SIZE)"
