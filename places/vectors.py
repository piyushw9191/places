import functools
import json
from multiprocessing import current_process
import traceback as tb

import aiohttp
import ujson
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

from txtai.pipeline import Summary
from txtai.pipeline.data import Segmentation

from places.utils import task_pool


model = SentenceTransformer("all-MiniLM-L6-v2")
summary = Summary("sshleifer/distilbart-cnn-12-6")
segmentation = Segmentation(sentences=True)


def json_error(func):
    @functools.wraps(func)
    def _json_error(*args, **kw):
        try:
            return func(*args, **kw)
        except Exception as e:
            return json.dumps(
                {
                    "error": repr(e),
                    "tb": "".join(tb.format_exception(None, e, e.__traceback__)),
                }
            )

    return _json_error


@json_error
def build_vector(data):
    """Vectorizes a page.

    1. Extracts the title and text using BeautifulSoup
    2. Segmentizes the text
    3. Create embeddings for each sentences using SentenceTransformer

    Accepts a JSON-encoded mapping containing the url and text.
    The data is passed as a string so it can be pickled.

    Returns a JSON-encoded mapping containing:

    - vectors: a list of vectors
    - sentences: a list of sentences
    - title: the title of the page

    """

    data = json.loads(data)
    url = data["url"]
    text = data["text"]

    cp = current_process()
    print(f"[extractor][{cp.pid}] working on {url}")
    soup = BeautifulSoup(text, "html.parser")

    try:
        title = soup.title
        if title is None:
            title = ""
        else:
            title = title.string
    except Exception:
        title = ""

    text = soup.get_text()

    if len(text) > 1024:
        text = summary(text)

    sentences = segmentation(text)
    vectors = model.encode(sentences)

    return json.dumps(
        {"vectors": vectors.tolist(), "sentences": sentences, "title": title}
    )


class Upserter:
    def __init__(self, queue, server="http://localhost:8080"):
        self.queue = queue
        self.server = server

    async def post_url(self, client, url, text):
        try:
            doc = {"url": url, "text": text}

            async with client.post(f"{self.server}/index", json=doc) as resp:
                res = await resp.json()
                if resp.status > 299:
                    print(res["error"])
                    return None
        except Exception as e:
            print(f"[scrap] Could not post {url} {e}")
            return None

    async def run(self):
        async with task_pool() as tasks:
            async with aiohttp.ClientSession(
                json_serialize=ujson.dumps, timeout=aiohttp.ClientTimeout(total=15)
            ) as client:
                while True:
                    res = await self.queue.get()
                    if res == "END":
                        await self.queue.put("END")
                        return

                    url, text = res

                    await tasks.put(functools.partial(self.post_url, client, url, text))
