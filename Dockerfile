# Same base image as GET.route (real Google Chrome 151), so on the NAS the
# two projects share it instead of storing Chrome twice.
FROM chetan1111/botasaurus:chrome-151

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROMEDRIVER_PATH=/usr/local/bin/chromedriver \
    CHROME_EXTRA_ARGS="--no-sandbox --disable-dev-shm-usage --disable-gpu --window-size=1366,900"

COPY requirements.txt requirements-server.txt ./
RUN python -m pip install --no-cache-dir -r requirements-server.txt

# Download the chromedriver for exactly this Chrome version, once, at build time.
RUN VERSION="$("$CHROME_BIN" --version | grep -oE '[0-9]+(\.[0-9]+){3}')" \
    && python - "$VERSION" "$CHROMEDRIVER_PATH" <<'PY'
import io, os, sys, urllib.request, zipfile
version, dest = sys.argv[1], sys.argv[2]
url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chromedriver-linux64.zip"
with zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url).read())) as z:
    with open(dest, "wb") as f:
        f.write(z.read("chromedriver-linux64/chromedriver"))
os.chmod(dest, 0o755)
PY
RUN "$CHROMEDRIVER_PATH" --version

RUN mkdir -p /app
WORKDIR /app
COPY TrafficScraper.py browser_client.py config.py routes.py run.py ./

EXPOSE 8000
CMD ["python", "run.py"]
