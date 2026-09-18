from __future__ import annotations

import json
import unittest

from personal_assistant_sdk.host import HostBroker
from personal_assistant_sdk.rpc import (
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_request,
    encode_frame,
)


class SdkRpcTests(unittest.TestCase):
    def test_round_trip_preserves_json_only_request(self) -> None:
        request = RpcRequest(id="call-1", method="tool.invoke", params={"value": 1})
        decoded = decode_request(encode_frame(request))
        self.assertEqual(request, decoded)

    def test_oversized_frame_fails_closed(self) -> None:
        frame = b'{"jsonrpc":"2.0","id":"x","method":"m","params":{}}\n'
        with self.assertRaises(RpcProtocolError):
            decode_request(frame, max_bytes=10)


class HostBrokerTimeoutContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_deadline_is_sent_to_the_host_as_well_as_applied_locally(self) -> None:
        frames: list[dict[str, object]] = []
        broker: HostBroker

        async def write_frame(frame: bytes) -> None:
            body = json.loads(frame)
            frames.append(body)
            broker.resolve(RpcResponse(id=str(body["id"]), result={"rows": [], "rowcount": 0}))

        broker = HostBroker(write_frame)
        await broker.execute("SELECT 1", timeout_seconds=1.25)
        await broker.transaction(
            [{"statement": "SELECT 1", "parameters": []}], timeout_seconds=2.5
        )
        await broker.migrate([], timeout_seconds=3.75)

        self.assertEqual(
            [1.25, 2.5, 3.75],
            [frame["params"]["timeout_seconds"] for frame in frames],  # type: ignore[index]
        )


if __name__ == "__main__":
    unittest.main()
