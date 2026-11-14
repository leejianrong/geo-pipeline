"""In-memory stand-ins for the object store and Valkey, for tests that need no Docker."""

import io

from botocore.exceptions import ClientError


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body, **_):  # noqa: N803  (boto3's argument names)
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": Key}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        return f"http://store/{Params['Bucket']}/{Params['Key']}?op={op}"


class FakeValkey:
    def __init__(self):
        self.sets: dict[str, dict[str, float]] = {}

    def zadd(self, key, mapping):
        self.sets.setdefault(key, {}).update(mapping)

    def zrange(self, key, start, stop):
        return [k for k, _ in sorted(self.sets.get(key, {}).items(), key=lambda kv: kv[1])]

    def hgetall(self, key):
        return {}

    def zrevrange(self, key, start, stop):
        return []

    def ping(self):
        return True
