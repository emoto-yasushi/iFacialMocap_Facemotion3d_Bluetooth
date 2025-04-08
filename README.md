# iFacialMocap_Facemotion3d_Bluetooth

This Python script is a sample code that enables real-time communication with the iOS apps iFacialMocap and Facemotion3d using Bluetooth.

It prints the same string as when communicating via UDP.
For details on the UDP communication specifications of iFacialMocap, please refer to the following page.

How to use:
Simply install the Bleak library by running `pip install bleak` in Python, and then execute the script.


In Facemotion3d, negative values may be sent as BlendShape values.
However, iFacialMocap is not designed to handle negative BlendShape values.
When you set bluetooth_instance.message_mode="iFacialMocap", it retrieves a string that ignores negative values.
When you set bluetooth_instance.message_mode="Facemotion3d", it retrieves a string that takes negative values into account.
