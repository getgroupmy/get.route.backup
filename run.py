"""Start the traffic API.

    python run.py            # http://localhost:8000
    PORT=9000 python run.py  # another port

Then:  curl "http://localhost:8000/routes/search?from=-34.9285,138.6007&to=-34.9462,138.5312"

The first query opens Chrome (see browser_client.py); it stays open between
queries and closes by itself after 10 idle minutes.
"""
import bottle
from cheroot import wsgi

import config
import routes  # noqa: F401  (mounts the routes on bottle's default app)


def main():
    app = bottle.default_app()
    print(f"Traffic API listening on http://localhost:{config.PORT}/")
    print(f"Try:  curl \"http://localhost:{config.PORT}/routes/search?from=-34.9285,138.6007&to=-34.9462,138.5312\"")
    if config.WARM_ON_START:
        from browser_client import chrome
        n = chrome.warm()
        print(f"Opening {n} Chrome window(s) now (WARM_ON_START=1); up to {config.CHROME_WINDOWS} as traffic needs")
    # One thread per Chrome window, plus some for /health and queued queries.
    threads = max(16, config.CHROME_WINDOWS + 16)
    server = wsgi.Server(("0.0.0.0", config.PORT), app, server_name="traffic-scraper", numthreads=threads)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
