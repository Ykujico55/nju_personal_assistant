from __future__ import annotations

import unittest

from personal_assistant_sdk.rpc import (
    RpcProtocolError,
    RpcRequest,
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


if __name__ == "__main__":
    unittest.main()

