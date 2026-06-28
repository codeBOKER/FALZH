from typing import Any

from app.database.supabase import SupabaseRepository
from app.services.embedding_service import JinaEmbeddingService
from app.utils.departure import trip_departure_bucket, trip_departure_date


def build_trip_embedding_record(trip: dict[str, Any], embedding_model: str) -> dict[str, Any]:
    driver = _first_or_dict(trip.get("drivers")) or {}
    car = _first_or_dict(trip.get("driver_cars")) or {}
    trip_id = str(trip.get("id") or trip.get("trip_id"))
    departure_date = trip_departure_date(trip)
    departure_time = trip_departure_bucket(trip)
    chunk_text = (
        f"Trip {trip_id}: {trip.get('departure')} to {trip.get('destination')} "
        f"on {departure_date} during {departure_time}. "
        f"Available seats: {trip.get('available_seats')} of {trip.get('total_seats')}. "
        f"Vehicle: {car.get('car_type')}. Driver: {driver.get('name')}. "
        f"Price: {trip.get('price')}. Status: {trip.get('status')}."
    )
    return {
        "trip_id": trip_id,
        "chunk_text": chunk_text,
        "embedding_model": embedding_model,
    }


async def index_trip(
    *,
    repository: SupabaseRepository,
    embeddings: JinaEmbeddingService,
    embedding_model: str,
    trip: dict[str, Any],
) -> None:
    record = build_trip_embedding_record(trip, embedding_model)
    record["embedding"] = (await embeddings.embed_passages([record["chunk_text"]]))[0]
    await repository.upsert_trip_embeddings([record])


async def unindex_trip(*, repository: SupabaseRepository, trip_id: str) -> None:
    await repository.delete_trip_embedding(trip_id)


def _first_or_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value
    return None
