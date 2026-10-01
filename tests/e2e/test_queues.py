import json
import uuid

import boto3
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sqs(e2e_settings):
    return boto3.client(
        "sqs",
        endpoint_url=e2e_settings.sqs_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


def test_click_events_queue_configuration(sqs):
    url = sqs.get_queue_url(QueueName="click-events")["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["VisibilityTimeout"] == "30"
    assert attrs["ReceiveMessageWaitTimeSeconds"] == "20"
    redrive = json.loads(attrs["RedrivePolicy"])
    assert int(redrive["maxReceiveCount"]) == 5
    assert redrive["deadLetterTargetArn"].endswith(":click-events-dlq")


def test_dlq_exists(sqs):
    assert sqs.get_queue_url(QueueName="click-events-dlq")["QueueUrl"]


def test_message_round_trip_with_attributes(sqs):
    # A scratch queue: the click-processor consumes click-events, so using it here would race.
    url = sqs.create_queue(QueueName=f"e2e-round-trip-{uuid.uuid4().hex[:8]}")["QueueUrl"]
    try:
        sqs.send_message(
            QueueUrl=url,
            MessageBody="{}",
            MessageAttributes={"type": {"DataType": "String", "StringValue": "e2e.ping"}},
        )
        received = sqs.receive_message(
            QueueUrl=url, MessageAttributeNames=["All"], WaitTimeSeconds=5
        )["Messages"]
        assert received[0]["MessageAttributes"]["type"]["StringValue"] == "e2e.ping"
    finally:
        sqs.delete_queue(QueueUrl=url)
