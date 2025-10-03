"""
Created by Yasushi Emoto
4/4/2025  
This Python script enables real-time communication using iFacialMocap, Facemotion3d, and Bluetooth.


Please execute pip install bleak before running the script.

In Facemotion3d, negative values may be sent as BlendShape values.
However, iFacialMocap is not designed to handle negative BlendShape values.
When you set bluetooth_instance.message_mode="iFacialMocap", it retrieves a string that ignores negative values.
When you set bluetooth_instance.message_mode="Facemotion3d", it retrieves a string that takes negative values into account.
"""

import queue
import threading
import struct
import zlib
import json
import uuid
import asyncio
import time
import traceback
from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic
import sys

class ifm_and_fm_bluetooh_functions:
    def __init__(self, bluetooth_id="default",message_mode="iFacialMocap"):
        self.bluetooth_id = bluetooth_id
        self.message_mode = message_mode
        self.NOTIFY_CHARACTERISTIC_UUID = "EFAB5678-1234-90AB-CDEF-1234567890AB"
        self.WRITE_CHARACTERISTIC_UUID = "ABCD1234-5678-90AB-CDEF-1234567890AB"
        self.bluetooth_connection_mode_queue = queue.Queue()

        self.bluetooth_is_sending = False
        self.bluetooth_message_queue = queue.Queue()
        self.bluetooth_thread = None
        self.bluetooth_client = None
        self.connect_button_default_background_color = None
        self.connection_mode = "realtime"
        self.bluetooth_message = queue.Queue(maxsize=200)
        self.bluetooth_loop = None
        self.connect_startTime = None
        self.recive_bluetooth_Mode = False
        self.wifi_recieve_counter = False
        self.bluetooth_thread_lock = threading.Lock()
        self.encode_data_size = None
        self.bluetooth_received_data = bytearray()
        self.decompress_json_data = {}
        self.data_ready_event = threading.Event()
        self.recieved_message = None
    
    def decode_blendshapes_data(self, data: bytes, index: int) -> (str, int):
        blend_shapes = self.decompress_json_data.get("blendShapes", [])
        blend_shape_count = len(blend_shapes)
        decoded = []
        
        for i in range(blend_shape_count):
            if index >= len(data):
                break
            encoded_value = data[index]
            index += 1
            if encoded_value != 255:
                value = encoded_value - 100
                if self.message_mode == "iFacialMocap":
                    value = max(0, value)
                    decoded.append(f"{blend_shapes[i]}-{value}")
                else:
                    decoded.append(f"{blend_shapes[i]}&{value}")
            else:
                if self.message_mode == "iFacialMocap":
                    decoded.append(f"{blend_shapes[i]}-0")
                else:
                    decoded.append(f"{blend_shapes[i]}&0")
        decoded_str = '|'.join(decoded)
        return decoded_str, index


    def decode_bones_data_0x02(self, data: bytes, index: int) -> (str, int):
        bones_list = self.decompress_json_data.get("bones", [])

        decoded = []
        for bone_info in bones_list:
            if len(bone_info) != 2:
                continue

            bone_name, count = bone_info[0], bone_info[1]

            bone_values = []
            for i in range(count):
                if index + 1 >= len(data):
                    break

                high_byte = data[index]
                low_byte = data[index + 1]
                index += 2

                uint16 = (high_byte << 8) | low_byte
                int16 = struct.unpack('>h', struct.pack('>H', uint16))[0]

                if bone_name == "head" and i >= 3:
                    value = int16 / 10000.0
                else:
                    value = int16 / 10.0
                bone_values.append(str(value))

            bone_values_str = ','.join(bone_values)
            decoded.append(f"{bone_name}#{bone_values_str}")

        decoded_str = '|'.join(decoded) + '|'
        return decoded_str, index
    
    def decode_bones_data_0x03(self, data: bytes, index: int) -> (str, int):
        bones_list = self.decompress_json_data.get("bones", [])

        decoded = []
        for bone_info in bones_list:
            if len(bone_info) != 2:
                continue

            bone_name, count = bone_info[0], bone_info[1]

            bone_values = []
            for _ in range(count):
                if index + 7 >= len(data):
                    break
                chunk = data[index:index+8]
                index += 8
                int64_val = struct.unpack('>q', chunk)[0]
                value = int64_val / 10_000_000.0
                bone_values.append(str(value))

            bone_values_str = ','.join(bone_values)
            decoded.append(f"{bone_name}#{bone_values_str}")

        decoded_str = '|'.join(decoded) + '|'
        return decoded_str, index

    def values_decode_data(self, data: bytes, data_size) -> str:
        index = 0
        blend_shape_message_list = ""
        joint_message_list = ""

        data = data[3: 3 + data_size]

        while index < len(data):
            identifier = data[index]
            index += 1
            if identifier == 0x01:
                try:
                    decoded_blend_shapes, index = self.decode_blendshapes_data(data, index)
                    blend_shape_message_list = decoded_blend_shapes
                except:
                    traceback.print_exc()
            elif identifier == 0x02:
                try:
                    decoded_bones, index = self.decode_bones_data_0x02(data, index)
                    joint_message_list = decoded_bones
                except:
                    traceback.print_exc()
            elif identifier == 0x03:
                try:
                    decoded_bones, index = self.decode_bones_data_0x03(data, index)
                    joint_message_list = decoded_bones
                except:
                    traceback.print_exc()
            else:
                traceback.print_exc()
                return ""
        
        return f"{blend_shape_message_list}={joint_message_list}"

    def decompress_zlib(self, compressed_data: bytes, data_size) -> str:
        try:
            decompressed_bytes = zlib.decompress(compressed_data)
            
            decompressed_text = decompressed_bytes.decode('utf-8')
            
            return decompressed_text
        except:
            traceback.print_exc()
            return None

    def generate_uuid_v5_from_string(self, namespace_uuid, input_string):
        namespace = uuid.UUID(namespace_uuid)
        name_bytes = input_string.encode('utf-8')
        generated_uuid = uuid.uuid5(namespace, name_bytes.decode('utf-8'))

        return str(generated_uuid)

    async def bluetooth_recieve_realtime_start(self, message):
        namespace_uuid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        SERVICE_UUID = self.generate_uuid_v5_from_string(namespace_uuid, self.bluetooth_id)

        def handle_notification(sender: BleakGATTCharacteristic, data: bytearray):
            if not self.recive_bluetooth_Mode:
                return
            with self.bluetooth_thread_lock:
                self.bluetooth_received_data.extend(data)
                try:
                    if self.bluetooth_received_data[0] == 0x00:
                        if len(self.bluetooth_received_data) > 3:
                            if self.encode_data_size is None:
                                self.encode_data_size = struct.unpack(">H", self.bluetooth_received_data[1:3])[0]
                            if len(self.bluetooth_received_data) >= 3 + self.encode_data_size:
                                compressed_data = self.bluetooth_received_data[3: 3 + self.encode_data_size]
                                decoded_string = self.decompress_zlib(compressed_data, self.encode_data_size)
                                self.decompress_json_data = json.loads(decoded_string)
                                self.bluetooth_received_data = bytearray()
                                self.encode_data_size = None

                    elif self.bluetooth_received_data[0] == 0x01:
                        if len(self.bluetooth_received_data) > 3:
                            if self.encode_data_size is None:
                                self.encode_data_size = struct.unpack(">H", self.bluetooth_received_data[1:3])[0]
                            if len(self.bluetooth_received_data) >= 3 + self.encode_data_size:
                                message = self.values_decode_data(self.bluetooth_received_data, self.encode_data_size)
                                if message is not None:
                                    try:
                                        self.recieved_message = message
                                    except queue.Full:
                                        traceback.print_exc()
                                self.bluetooth_received_data = bytearray()
                                self.encode_data_size = None

                except Exception:
                    self.bluetooth_received_data = bytearray()
                    self.encode_data_size = None
                    traceback.print_exc()

        try:
            devices = await BleakScanner.discover()
            if not devices:
                print("No Bluetooth devices found.")
                self.bluetooth_stop()
                return

            for device in devices:
                uuids = getattr(device, "metadata", {}).get("uuids", []) or []
                try_this = (SERVICE_UUID.lower() in [s.lower() for s in uuids]) or True 

                if not try_this:
                    continue

                print(f"Connecting to: {device.name} ({device.address})")

                if self.bluetooth_client and self.bluetooth_client.is_connected:
                    await self.bluetooth_send_message(message)
                    return

                client = BleakClient(device, timeout=35.0)
                try:
                    await client.connect()
                    await client.get_services()

                    has_notify = client.services.get_characteristic(self.NOTIFY_CHARACTERISTIC_UUID) is not None
                    has_write  = client.services.get_characteristic(self.WRITE_CHARACTERISTIC_UUID) is not None
                    if not (has_notify and has_write):
                        await client.disconnect()
                        continue

                    print("Successfully connected to Bluetooth.")
                    self.bluetooth_client = client

                    await client.start_notify(self.NOTIFY_CHARACTERISTIC_UUID, handle_notification)
                    await asyncio.sleep(0.5)
                    await self.bluetooth_send_message(message)

                    self.process_message_queue()
                    break

                except Exception:
                    self.bluetooth_stop()
                    traceback.print_exc()
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except:
                        pass
                    self.bluetooth_client = None

        except Exception:
            self.bluetooth_stop()
            traceback.print_exc()

                
    async def bluetooth_send_message(self, message):
        try:
            self.bluetooth_received_data.clear()
        except:
            pass

        if self.bluetooth_client and self.bluetooth_client.is_connected:
            try:
                await self.bluetooth_client.write_gatt_char(
                     self.WRITE_CHARACTERISTIC_UUID,
                     bytearray(message, 'utf-8'),
                     response=True
                 )
            except Exception as e:
                print(f"Error sending message: {e}")
                traceback.print_exc()
        else:
            print("Not connected to Bluetooth device.")


    def bluetooth_send_message_trigger(self, message):
        self.recive_bluetooth_Mode = True
        
        if self.bluetooth_thread is None or not self.bluetooth_thread.is_alive():
            self.bluetooth_thread = threading.Thread(target=self.run_bluetooth_loop, args=(message,), daemon=True)
            self.bluetooth_thread.start()
            time.sleep(0.1) 
        else:
            self.bluetooth_message_queue.put(message)
        
        if not self.bluetooth_is_sending:
            self.process_message_queue()

    def process_message_queue(self):
        if self.bluetooth_is_sending or self.bluetooth_message_queue.empty():
            return

        self.bluetooth_is_sending = True
        message = self.bluetooth_message_queue.get()

        try:
            max_wait_time = 5
            start_time = time.time()
            while not (self.bluetooth_client and self.bluetooth_client.is_connected):
                if time.time() - start_time > max_wait_time:
                    print(f"Timeout waiting for Bluetooth connection for message '{message}'.")
                    self.bluetooth_message_queue.put(message)
                    self.bluetooth_is_sending = False
                    return
                time.sleep(0.1)

            if self.bluetooth_client and self.bluetooth_client.is_connected:
                future = asyncio.run_coroutine_threadsafe(self.bluetooth_send_message(message), self.bluetooth_loop)
                future.result(timeout=2.0)
                print(f"Successfully sent message: {message}")
            else:
                print(f"Bluetooth not connected for message '{message}'. Queuing for later.")

        except asyncio.TimeoutError:
            print(f"Timeout sending message '{message}'. Retrying later.")
            self.bluetooth_message_queue.put(message)
        except Exception:
            print(f"Error processing message '{message}':")
            traceback.print_exc()
            self.bluetooth_message_queue.put(message)
        finally:
            self.bluetooth_is_sending = False
            if not self.bluetooth_message_queue.empty():
                print(f"Queue not empty, continuing processing...")
                self.process_message_queue()

    def run_bluetooth_loop(self, message):
        self.bluetooth_message = queue.Queue()
        self.bluetooth_received_data = bytearray()
        self.decompress_json_data = {}

        if not hasattr(self, 'bluetooth_loop') or self.bluetooth_loop is None:
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            self.bluetooth_connection_mode_queue.put("connecting")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.bluetooth_loop = loop
            threading.Thread(target=loop.run_forever, daemon=True).start()
        else:
            loop = self.bluetooth_loop

        try:
            if not self.bluetooth_client or not self.bluetooth_client.is_connected:
                future = asyncio.run_coroutine_threadsafe(self.bluetooth_recieve_realtime_start(message), loop)
                future.result(timeout=30.0)
            else:
                future = asyncio.run_coroutine_threadsafe(self.bluetooth_send_message(message), loop)
                future.result(timeout=2.0)

            if self.recive_bluetooth_Mode:
                asyncio.run_coroutine_threadsafe(self.keep_bluetooth_alive(message), loop)

        except Exception as e:
            print(f"Bluetooth loop error: {e}")
            traceback.print_exc()

    async def keep_bluetooth_alive(self, message):
        while self.recive_bluetooth_Mode:
            if not self.bluetooth_client or not self.bluetooth_client.is_connected:
                await self.bluetooth_recieve_realtime_start(message)
            await asyncio.sleep(1.0)

    async def stop_async_tasks(self):
        try:
            if self.bluetooth_loop:
                if hasattr(self, 'scanning_task') and self.scanning_task:
                    self.scanning_task.cancel()
                    try:
                        await self.scanning_task
                    except asyncio.CancelledError:
                        pass
                    self.scanning_task = None

                tasks = [t for t in asyncio.all_tasks(self.bluetooth_loop) if
                            t is not asyncio.current_task(self.bluetooth_loop)]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except:
            pass

    async def stop_notifications_and_cleanup(self):
        try:
            if self.bluetooth_client and self.bluetooth_client.is_connected:
                if hasattr(self, 'notify_char_handle') and self.notify_char_handle is not None:
                    await self.bluetooth_client.stop_notify(self.notify_char_handle)
                await self.bluetooth_client.disconnect()
        except Exception as e:
            print(f"Error during cleanup: {e}")
                    
    def bluetooth_stop(self):
        try:
            self.recive_bluetooth_Mode = False

            if self.bluetooth_loop and self.bluetooth_loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(self.stop_async_tasks(), self.bluetooth_loop)
                    future.result(timeout=5.0)
                except TimeoutError:
                    print("Timeout stopping async tasks.")
                except Exception as e:
                    print(f"Error stopping async tasks: {e}")
                    traceback.print_exc()
                try:
                    future = asyncio.run_coroutine_threadsafe(self.stop_notifications_and_cleanup(), self.bluetooth_loop)
                    future.result(timeout=5.0) 
                except TimeoutError:
                    print("Timeout stopping notifications and cleanup.")
                except Exception as e:
                    print(f"Error stopping notifications and cleanup: {e}")
                    traceback.print_exc()

                try:
                    self.bluetooth_loop.call_soon_threadsafe(self.bluetooth_loop.stop)
                    start_time = time.time()
                    while self.bluetooth_loop.is_running():
                        if time.time() - start_time > 5:
                            print("Bluetooth loop stop timeout")
                            break
                        time.sleep(0.1)
                except:
                    traceback.print_exc()

            if self.bluetooth_thread and self.bluetooth_thread.is_alive():
                self.bluetooth_thread.join(timeout=1.0)
                self.bluetooth_thread = None

            if self.bluetooth_message:
                while not self.bluetooth_message.empty():
                    self.bluetooth_message.get_nowait()

            self.bluetooth_received_data.clear()
            self.decompress_json_data = {}

        except Exception as e:
            print(f"Error during bluetooth stop: {e}")
            traceback.print_exc()




if __name__ == "__main__":
    bluetooth_id = "default"
    bluetooth_instance = ifm_and_fm_bluetooh_functions(bluetooth_id)

    #first send message------------------------------------------------------------------
    bluetooth_instance.bluetooth_send_message_trigger("iFacialMocap_sahuasouryya9218sauhuiayeta91555dy3719")
    bluetooth_instance.message_mode = "iFacialMocap"

    print()

    #recieve message(5 times)
    count = 0
    while True:
        if bluetooth_instance.recieved_message:
            print("iFM recieved message: ", bluetooth_instance.recieved_message)
            print()
            time.sleep(0.016)
            count += 1
            if count > 5:
                break

    if bluetooth_instance.recive_bluetooth_Mode == True:
        bluetooth_instance.bluetooth_send_message_trigger("StopStreaming")
        bluetooth_instance.recive_bluetooth_Mode = False


    #second send message------------------------------------------------------------------
    bluetooth_instance.bluetooth_send_message_trigger("iFacialMocap_sahuasouryya9218sauhuiayeta91555dy3719")
    bluetooth_instance.message_mode = "Facemotion3d"

    #recieve message(5 times)
    count = 0
    while True:
        if bluetooth_instance.recieved_message:
            print("FM recieved message: ", bluetooth_instance.recieved_message)
            print()
            time.sleep(0.016)
            count += 1
            if count >= 5:
                break

    if bluetooth_instance.recive_bluetooth_Mode == True:
        bluetooth_instance.bluetooth_send_message_trigger("StopStreaming")
        bluetooth_instance.recive_bluetooth_Mode = False

    #stop bluetooth------------------------------------------------------------------
    bluetooth_instance.bluetooth_stop()
