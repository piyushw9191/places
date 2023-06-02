import asyncio
import os
import json
from uuid import uuid4
import logging
from concurrent.futures import ProcessPoolExecutor

from aiohttp import web
import numpy
from jinja2 import Environment, FileSystemLoader
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from sentence_transformers import SentenceTransformer

from places.extractor import build_vector


HERE = os.path.dirname(__file__)
COLLECTION_NAME = "pages"
routes = web.RouteTableDef()


@routes.post("/index")
async def index_doc(request):
    loop = asyncio.get_running_loop()

    try:
        data = await request.json()

        # CPU-bound
        task = loop.run_in_executor(
            request.app.executor, build_vector, data["url"], data["text"]
        )
        await task

        if task.exception() is not None:
            raise task.exception()

        vectors, sentences, summary = task.result()
        points = []

        for vec, sentence in zip(vectors, sentences, strict=True):
            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector=list(numpy.asfarray(vec)),
                    payload={"url": data["url"], "sentence": sentence},
                )
            )

        # TODO IO-bound, should be done in an async call (qdrant_python supports this)
        return request.app.json_resp(
            request.app.client.upsert(
                collection_name=COLLECTION_NAME,
                points=points,
            ).json()
        )
    except Exception as e:
        return request.app.json_resp({"error": str(e)}, 400)


@routes.get("/search")
async def search(request):
    q = request.query["q"]
    hits = await request.app.query(q)
    args = {
        "args": {"title": "Private Search"},
        "description": "Search Your History",
        "hits": hits,
        "query": q,
    }
    return request.app.html_resp("index.html", **args)


@routes.get("/")
async def index(request):
    args = {"args": {"title": "Private Search"}, "description": "Search Your History"}
    return request.app.html_resp("index.html", **args)


class PlacesApplication(web.Application):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")))
        self.client = QdrantClient(host="localhost", port=6333)
        self.executor = ProcessPoolExecutor()

    async def on_shutdown(self, app):
        self.executor.shutdown()

    async def query(self, sentence):
        # vectorize the query
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(self.executor, self.model.encode, [sentence])
        await task
        if task.exception() is not None:
            raise task.exception()
        embedding = task.result()

        vector = numpy.asfarray(embedding[0])
        vector = list(vector)
        # should move to i-o bound
        hits = self.client.search(
            collection_name=COLLECTION_NAME, query_vector=vector, limit=3
        )
        return hits

    def html_resp(self, template, status=200, **args):
        template = self.env.get_template(template)
        content = template.render(**args)
        resp = web.Response(text=content)
        resp.headers["Content-Type"] = "text/html"
        resp.set_status(status)
        return resp

    def json_resp(self, body, status=200):
        if not isinstance(body, str):
            body = json.dumps(body)
        resp = web.Response(text=body)
        resp.headers["Content-Type"] = "application/json"
        resp.set_status(status)
        return resp


def main():
    logging.getLogger("asyncio").setLevel(logging.DEBUG)
    app = PlacesApplication()
    app.add_routes(routes)
    web.run_app(app, port=8080)


if __name__ == "__main__":
    main()
