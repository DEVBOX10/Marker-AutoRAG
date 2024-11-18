import logging

from datetime import timedelta


from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions

from typing import List, Tuple

from autorag.vectordb import BaseVectorStore

logger = logging.getLogger("AutoRAG")


class Couchbase(BaseVectorStore):
	def __init__(
		self,
		embedding_model: str,
		collection_name: str,
		embedding_batch: int = 100,
		similarity_metric: str = "cosine",
		connection_string: str = "couchbase://localhost",
		username: str = "",
		password: str = "",
		batch_size: int = 100,
	):
		super().__init__(embedding_model, similarity_metric, embedding_batch)

		auth = PasswordAuthenticator(username, password)
		self.cluster = Cluster(connection_string, ClusterOptions(auth))

		# Wait until the cluster is ready for use.
		self.cluster.wait_until_ready(timedelta(seconds=5))

	async def add(self, ids: List[str], texts: List[str]):
		texts = self.truncated_inputs(texts)
		text_embeddings = await self.embedding.aget_text_embedding_batch(texts)

		with self.client.batch.dynamic() as batch:
			for i, text in enumerate(texts):
				data_properties = {self.text_key: text}

				batch.add_object(
					collection=self.collection_name,
					properties=data_properties,
					uuid=ids[i],
					vector=text_embeddings[i],
				)

		failed_objs = self.client.batch.failed_objects
		for obj in failed_objs:
			err_message = (
				f"Failed to add object: {obj.original_uuid}\nReason: {obj.message}"
			)

			logger.error(err_message)

	async def fetch(self, ids: List[str]) -> List[List[float]]:
		# Fetch vectors by IDs
		results = self.collection.query.fetch_objects(
			filters=wvc.query.Filter.by_property("_id").contains_any(ids),
			include_vector=True,
		)
		id_vector_dict = {
			str(object.uuid): object.vector["default"] for object in results.objects
		}
		result = [id_vector_dict[_id] for _id in ids]
		return result

	async def is_exist(self, ids: List[str]) -> List[bool]:
		fetched_result = self.collection.query.fetch_objects(
			filters=wvc.query.Filter.by_property("_id").contains_any(ids),
		)
		existed_ids = [str(result.uuid) for result in fetched_result.objects]
		return list(map(lambda x: x in existed_ids, ids))

	async def query(
		self, queries: List[str], top_k: int, **kwargs
	) -> Tuple[List[List[str]], List[List[float]]]:
		queries = self.truncated_inputs(queries)
		query_embeddings: List[
			List[float]
		] = await self.embedding.aget_text_embedding_batch(queries)

		ids, scores = [], []
		for query_embedding in query_embeddings:
			response = self.collection.query.near_vector(
				near_vector=query_embedding,
				limit=top_k,
				return_metadata=MetadataQuery(distance=True),
			)

			ids.append([o.uuid for o in response.objects])
			scores.append(
				[
					distance_to_score(o.metadata.distance, self.similarity_metric)
					for o in response.objects
				]
			)

		return ids, scores

	async def delete(self, ids: List[str]):
		filter = wvc.query.Filter.by_id().contains_any(ids)
		self.collection.data.delete_many(where=filter)

	def delete_collection(self):
		# Delete the collection
		self.client.collections.delete(self.collection_name)


def distance_to_score(distance: float, similarity_metric) -> float:
	if similarity_metric == "cosine":
		return 1 - distance
	elif similarity_metric == "ip":
		return -distance
	elif similarity_metric == "l2":
		return -distance
	else:
		raise ValueError(
			f"similarity_metric {similarity_metric} is not supported\n"
			"supported similarity metrics are: cosine, ip, l2"
		)
