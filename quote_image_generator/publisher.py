"""Use the hardened submodule and checkpoint its container before publishing."""

import time

from quote_image_generator.deadline import Deadline, request
from quote_image_generator.facebook_token_provider import (
    configure_facebook_token_from_provider,
)


def wait_until_ready(creation_id, *, publisher, get=request, seconds=60):
    deadline = Deadline(seconds)
    while True:
        response = get(
            "GET",
            publisher._graph_base() + "/" + creation_id,
            deadline=deadline,
            params={"fields": "status_code"},
            headers={"Authorization": "Bearer " + publisher._token()},
        )
        payload = response.json()
        status = payload.get("status_code") if isinstance(payload, dict) else None
        if status == "FINISHED":
            return
        if status != "IN_PROGRESS":
            raise publisher.PublishingError(
                "Container is not eligible for publication; reconcile it before another attempt."
            )
        time.sleep(min(1, deadline.remaining()))


def prepare():
    from upload_photo import upload_photo as publisher

    configure_facebook_token_from_provider()
    publisher._graph_base()
    publisher._account()
    publisher._token()
    for name in (
        "S3_BUCKET_NAME",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
    ):
        publisher._required(name)


def alert(subject, body):
    from upload_photo.upload_photo import send_email_alert

    return send_email_alert(subject, body)


def publish(path, caption, *, checkpoint):
    from upload_photo import upload_photo as publisher

    image_url = publisher.upload_image(path)
    container = publisher.create_media_container(image_url, caption)
    checkpoint(container)
    wait_until_ready(container, publisher=publisher)
    payload = publisher.publish_media_container(container)
    return publisher.PublishResult(media_id=payload["id"], creation_id=container)


publish.supports_checkpoint = True
