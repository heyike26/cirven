"""Real installed Win SDK DTO/import tests. Never creates a driver/session."""
import asyncio
import dataclasses
import os
import unittest

from packages.adapters.cua_desktop_input import _NativeSession, _json_result


@unittest.skipUnless(os.name == 'nt', 'released Windows SDK ABI only')
class OriginalSDKContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cua_driver
        cls.sdk = cua_driver
        if cua_driver.__version__ != '0.35.2':
            raise RuntimeError('Exact frozen Cua SDK version required')

    def tool_result(self):
        sdk = self.sdk
        return sdk.ToolResult(text='observation only', images=[sdk.ImageContent(mime_type='image/png', data_base64='original')],
            structured_json='{"capture_id":"captured-original"}', is_error=False, error_code=None,
            action=None, verification=None, degraded=False, raw_json='{}')

    def test_real_generated_record_fields_do_not_silently_disappear(self):
        result = self.tool_result()
        self.assertEqual(dataclasses.fields(result), ())
        parsed = _json_result(result)
        self.assertIs(parsed.get('is_error'), False)
        self.assertEqual(parsed['images'][0]['data_base64'], 'original')
        self.assertEqual(parsed['structured_json'], result.structured_json)

    def test_released_click_and_keyboard_input_records_exact_target(self):
        sdk = self.sdk
        captured = []
        async def receive_click(value):
            captured.append(value)
            return sdk.ActionResult(effect=sdk.ActionEffect.CONFIRMED, route=sdk.ActionRoute.SYNTHETIC_EVENTS,
                delivery=None, evidence=None, escalation=None, summary='ABI fixture only', error=None)
        async def receive_tool(value):
            captured.append(value)
            return self.tool_result()
        class Receiver:
            click = staticmethod(receive_click)
            type_text = staticmethod(receive_tool)
            press_key = staticmethod(receive_tool)
        session = _NativeSession(sdk, Receiver())
        async def exercise():
            window = {'kind': 'window', 'pid': 42, 'window_id': 199}
            r = await session.action('click', {'target': window, 'x': 2.0, 'y': 3.0, 'capture_id': 'captured-original', 'delivery_mode': 'background'})
            self.assertEqual(r['action']['effect'], 'CONFIRMED')
            await session.action('type_text', {'target': window, 'text': '中文真实资料'})
            await session.action('press_key', {'target': window, 'key': 'ENTER'})
            await session.action('click', {'target': {'kind': 'desktop', 'display_id': 'primary'},
                'x': 2.0, 'y': 3.0, 'capture_id': 'desktop-original', 'delivery_mode': 'foreground'})
        asyncio.run(exercise())
        self.assertIsInstance(captured[0], sdk.ClickInput)
        self.assertIsInstance(captured[0].target, sdk.ActionTarget.WINDOW)
        self.assertEqual((captured[0].target.pid, captured[0].target.window_id), (42, 199))
        self.assertIsInstance(captured[0].position, sdk.ClickPosition.CAPTURED_COORDINATES)
        self.assertEqual(captured[0].delivery_mode, sdk.InputDeliveryMode.BACKGROUND)
        self.assertIsInstance(captured[1], sdk.TypeTextInput)
        self.assertIsInstance(captured[2], sdk.PressKeyInput)
        self.assertIsInstance(captured[3].target, sdk.ActionTarget.DESKTOP)
        self.assertEqual(captured[3].target.display_id, 'primary')


if __name__ == '__main__':
    unittest.main()
