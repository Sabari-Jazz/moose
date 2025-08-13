import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import { v4 as uuidv4 } from 'uuid';
import { API_URL } from '@/constants/api';

// Storage keys
const DEVICE_ID_KEY = 'device_id';
const PUSH_TOKEN_KEY = 'expo_push_token';

// Device registration interface
interface DeviceRegistrationData {
  user_id: string;
  device_id: string;
  expo_push_token: string;
  platform: string;
}

/**
 * Get or generate a persistent device ID
 */
export async function getOrCreateDeviceId(): Promise<string> {
  try {
    // Try to get existing device ID
    let deviceId = await AsyncStorage.getItem(DEVICE_ID_KEY);
    
    if (!deviceId) {
      // Generate new UUID-based device ID
      deviceId = uuidv4();
      await AsyncStorage.setItem(DEVICE_ID_KEY, deviceId);
      console.log(`Generated new device ID: ${deviceId}`);
    } else {
      console.log(`Using existing device ID: ${deviceId}`);
    }
    
    return deviceId;
  } catch (error) {
    console.error('Error getting/creating device ID:', error);
    // Fallback to timestamp-based ID if UUID fails
    const fallbackId = `device_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    console.log(`Using fallback device ID: ${fallbackId}`);
    return fallbackId;
  }
}

/**
 * Register for push notifications and get Expo push token
 */
export async function getExpoPushToken(): Promise<string | null> {
  const isAndroid = Platform.OS === 'android';
  const isIOS = Platform.OS === 'ios';
  
  try {
    if (isAndroid) {
      console.log('AAAANDROID: 🤖 ===== STARTING ANDROID PUSH TOKEN REGISTRATION =====');
      console.log('AAAANDROID: Platform detected:', Platform.OS);
      console.log('AAAANDROID: Device info:', {
        isDevice: Device.isDevice,
        deviceName: Device.deviceName,
        modelName: Device.modelName,
        osVersion: Device.osVersion,
        deviceType: Device.deviceType
      });
    } else if (isIOS) {
      console.log('📱 Starting iOS push token registration process');
    }
    
    // Check if device is physical device
    if (!Device.isDevice) {
      if (isAndroid) {
        console.log('AAAANDROID: ❌ FATAL: Must use physical Android device for Push Notifications');
        console.log('AAAANDROID: Current device type:', Device.deviceType);
        console.log('AAAANDROID: This is probably an emulator/simulator');
      } else {
        console.log('Must use physical device for Push Notifications');
      }
      return null;
    }

    if (isAndroid) {
      console.log('AAAANDROID: ✅ Physical Android device confirmed');
      console.log('AAAANDROID: 📢 Setting up Android notification channel...');
    }

    // Set notification channel for Android
    if (isAndroid) {
      try {
        await Notifications.setNotificationChannelAsync('default', {
          name: 'default',
          importance: Notifications.AndroidImportance.MAX,
          vibrationPattern: [0, 250, 250, 250],
          lightColor: '#FF231F7C',
        });
        console.log('AAAANDROID: ✅ Android notification channel created successfully');
      } catch (channelError) {
        console.error('AAAANDROID: ❌ Failed to create notification channel:', channelError);
      }
    }

    // Check existing permissions
    if (isAndroid) {
      console.log('AAAANDROID: 🔍 Checking existing notification permissions...');
    }
    
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;

    if (isAndroid) {
      console.log('AAAANDROID: Existing permission status:', existingStatus);
      console.log('AAAANDROID: Permission details:', {
        status: existingStatus,
        granted: existingStatus === 'granted',
        canAskAgain: existingStatus !== 'denied'
      });
    }

    // Request permissions if not granted
    if (existingStatus !== 'granted') {
      if (isAndroid) {
        console.log('AAAANDROID: 📝 Permission not granted, requesting permissions...');
        console.log('AAAANDROID: This will show system permission dialog to user');
      }
      
      const permissionRequest = await Notifications.requestPermissionsAsync();
      finalStatus = permissionRequest.status;
      
      if (isAndroid) {
        console.log('AAAANDROID: Permission request completed');
        console.log('AAAANDROID: Permission changed from', existingStatus, 'to', finalStatus);
        console.log('AAAANDROID: Full permission response:', permissionRequest);
      }
    } else {
      if (isAndroid) {
        console.log('AAAANDROID: ✅ Permissions already granted');
      }
    }

    if (finalStatus !== 'granted') {
      if (isAndroid) {
        console.log('AAAANDROID: ❌ FATAL: Failed to get push token - permissions denied');
        console.log('AAAANDROID: Final status:', finalStatus);
        console.log('AAAANDROID: This means user denied permissions or device policy prevents notifications');
        console.log('AAAANDROID: User needs to go to Settings → Apps → [Your App] → Notifications → Enable');
      } else {
        console.log('Failed to get push token for push notification!');
      }
      return null;
    }

    if (isAndroid) {
      console.log('AAAANDROID: ✅ Permissions granted successfully');
      console.log('AAAANDROID: 🎯 Attempting to get Expo push token...');
      console.log('AAAANDROID: Using project ID: f8b79784-8f4b-42a9-aa3c-e8a901abba87');
    }

    // Get the push token
    const tokenStartTime = Date.now();
    const tokenResponse = await Notifications.getExpoPushTokenAsync({
      projectId: 'f8b79784-8f4b-42a9-aa3c-e8a901abba87',
    });
    const tokenEndTime = Date.now();
    
    const token = tokenResponse.data;

    if (isAndroid) {
      console.log('AAAANDROID: 🎉 Expo push token obtained successfully!');
      console.log('AAAANDROID: Token generation took:', (tokenEndTime - tokenStartTime), 'ms');
      console.log('AAAANDROID: Token preview:', token.substring(0, 50) + '...');
      console.log('AAAANDROID: Token length:', token.length);
      console.log('AAAANDROID: Token starts with ExponentPushToken:', token.startsWith('ExponentPushToken['));
      console.log('AAAANDROID: Token ends properly:', token.endsWith(']'));
      console.log('AAAANDROID: Full token response type:', typeof tokenResponse);
      console.log('AAAANDROID: Full token response keys:', Object.keys(tokenResponse));
      
      // Validate token format
      const tokenPattern = /^ExponentPushToken\[[A-Za-z0-9_-]+\]$/;
      const isValidFormat = tokenPattern.test(token);
      console.log('AAAANDROID: Token format valid:', isValidFormat);
      
      if (!isValidFormat) {
        console.log('AAAANDROID: ⚠️ WARNING: Token format seems invalid');
        console.log('AAAANDROID: Expected: ExponentPushToken[...], Got:', token.substring(0, 30) + '...');
      }
    } else if (isIOS) {
      console.log('📱 Expo push token obtained:', token);
    }
    
    // Store token locally for reference
    if (isAndroid) {
      console.log('AAAANDROID: 💾 Storing token in AsyncStorage...');
      console.log('AAAANDROID: Storage key:', PUSH_TOKEN_KEY);
    }
    
    await AsyncStorage.setItem(PUSH_TOKEN_KEY, token);
    
    if (isAndroid) {
      console.log('AAAANDROID: ✅ Token stored in AsyncStorage successfully');
      console.log('AAAANDROID: 🎉 ===== ANDROID PUSH TOKEN REGISTRATION COMPLETED =====');
    }
    
    return token;
  } catch (error: any) {
    if (isAndroid) {
      console.error('AAAANDROID: ❌ ===== ANDROID PUSH TOKEN ERROR =====');
      console.error('AAAANDROID: Error type:', typeof error);
      console.error('AAAANDROID: Error name:', error?.name);
      console.error('AAAANDROID: Error message:', error?.message);
      console.error('AAAANDROID: Error code:', error?.code);
      console.error('AAAANDROID: Error stack:', error?.stack);
      console.error('AAAANDROID: Full error object:', error);
      console.error('AAAANDROID: ===== END ANDROID ERROR LOG =====');
    } else {
      console.error('Error getting push token:', error);
    }
    return null;
  }
}

/**
 * Register device with backend API
 */
export async function registerDeviceWithBackend(userId: string): Promise<boolean> {
  const isAndroid = Platform.OS === 'android';
  const isIOS = Platform.OS === 'ios';
  
  try {
    if (isAndroid) {
      console.log('AAAANDROID: 🔧 ===== STARTING ANDROID DEVICE REGISTRATION =====');
      console.log(`AAAANDROID: Starting device registration for user: ${userId}`);
      console.log('AAAANDROID: Platform:', Platform.OS);
    } else if (isIOS) {
      console.log(`📱 Starting device registration for user: ${userId}`);
    }
    
    // Get or create device ID
    if (isAndroid) {
      console.log('AAAANDROID: 🆔 Getting or creating device ID...');
    }
    
    const deviceId = await getOrCreateDeviceId();
    
    if (isAndroid) {
      console.log('AAAANDROID: Device ID obtained:', deviceId);
      console.log('AAAANDROID: Device ID length:', deviceId.length);
      console.log('AAAANDROID: Device ID is UUID format:', /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(deviceId));
    }
    
    // Get push token
    if (isAndroid) {
      console.log('AAAANDROID: 🎯 Getting push token...');
    }
    
    const pushTokenStartTime = Date.now();
    const pushToken = await getExpoPushToken();
    const pushTokenEndTime = Date.now();
    
    if (!pushToken) {
      if (isAndroid) {
        console.log('AAAANDROID: ❌ FATAL: No push token available, skipping device registration');
        console.log('AAAANDROID: This means getExpoPushToken() failed');
        console.log('AAAANDROID: Check the logs above for push token errors');
      } else {
        console.log('No push token available, skipping device registration');
      }
      return false;
    }

    if (isAndroid) {
      console.log('AAAANDROID: ✅ Push token obtained successfully');
      console.log('AAAANDROID: Push token generation took:', (pushTokenEndTime - pushTokenStartTime), 'ms');
      console.log('AAAANDROID: Push token preview:', `${pushToken.substring(0, 30)}...`);
    }

    // Prepare registration data
    const platform = isAndroid ? 'android' : 'iOS';
    const registrationData: DeviceRegistrationData = {
      user_id: userId,
      device_id: deviceId,
      expo_push_token: pushToken,
      platform: platform,
    };

    if (isAndroid) {
      console.log('AAAANDROID: 📝 Registration data prepared:');
      console.log('AAAANDROID: - User ID:', registrationData.user_id);
      console.log('AAAANDROID: - Device ID:', registrationData.device_id);
      console.log('AAAANDROID: - Platform:', registrationData.platform);
      console.log('AAAANDROID: - Token preview:', `${pushToken.substring(0, 20)}...`);
      console.log('AAAANDROID: - Token length:', pushToken.length);
      console.log('AAAANDROID: - API URL:', API_URL);
      console.log('AAAANDROID: - Full endpoint:', `${API_URL}/api/device/register`);
    } else {
      console.log(`Registering device with backend:`, {
        user_id: registrationData.user_id,
        device_id: registrationData.device_id,
        platform: registrationData.platform,
        token_preview: `${pushToken.substring(0, 20)}...`
      });
    }

    // Send registration to backend
    if (isAndroid) {
      console.log('AAAANDROID: 🌐 Sending registration request to backend...');
      console.log('AAAANDROID: Method: POST');
      console.log('AAAANDROID: Headers: Content-Type: application/json');
    }
    
    const requestStartTime = Date.now();
    const response = await fetch(`${API_URL}/api/device/register`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(registrationData),
    });
    const requestEndTime = Date.now();

    if (isAndroid) {
      console.log('AAAANDROID: 📡 Backend request completed');
      console.log('AAAANDROID: Request took:', (requestEndTime - requestStartTime), 'ms');
      console.log('AAAANDROID: Response status:', response.status);
      console.log('AAAANDROID: Response ok:', response.ok);
      console.log('AAAANDROID: Response headers:', Object.fromEntries(response.headers.entries()));
    }

    if (!response.ok) {
      const errorText = await response.text();
      if (isAndroid) {
        console.error('AAAANDROID: ❌ DEVICE REGISTRATION FAILED');
        console.error('AAAANDROID: Status code:', response.status);
        console.error('AAAANDROID: Status text:', response.statusText);
        console.error('AAAANDROID: Error response body:', errorText);
        console.error('AAAANDROID: This could be:');
        console.error('AAAANDROID: - Backend server is down');
        console.error('AAAANDROID: - Wrong API URL');
        console.error('AAAANDROID: - Backend validation error');
        console.error('AAAANDROID: - Network connectivity issue');
      } else {
        console.error(`Device registration failed: ${response.status} - ${errorText}`);
      }
      return false;
    }

    const result = await response.json();
    
    if (isAndroid) {
      console.log('AAAANDROID: ✅ DEVICE REGISTRATION SUCCESSFUL!');
      console.log('AAAANDROID: Backend response:', result);
      console.log('AAAANDROID: Success message:', result.message);
      console.log('AAAANDROID: Device ID confirmed:', result.device_id);
      console.log('AAAANDROID: 🎉 ===== ANDROID DEVICE REGISTRATION COMPLETED =====');
    } else {
      console.log('Device registration successful:', result.message);
    }
    
    return true;
  } catch (error: any) {
    if (isAndroid) {
      console.error('AAAANDROID: ❌ ===== ANDROID DEVICE REGISTRATION ERROR =====');
      console.error('AAAANDROID: Error type:', typeof error);
      console.error('AAAANDROID: Error name:', error?.name);
      console.error('AAAANDROID: Error message:', error?.message);
      console.error('AAAANDROID: Error code:', error?.code);
      console.error('AAAANDROID: Error stack:', error?.stack);
      console.error('AAAANDROID: Full error object:', error);
      console.error('AAAANDROID: This could be:');
      console.error('AAAANDROID: - Network timeout');
      console.error('AAAANDROID: - JSON parsing error');
      console.error('AAAANDROID: - Fetch API error');
      console.error('AAAANDROID: - Device ID generation error');
      console.error('AAAANDROID: ===== END ANDROID REGISTRATION ERROR =====');
    } else {
      console.error('Error registering device with backend:', error);
    }
    return false;
  }
}

/**
 * Get stored device ID (without creating new one)
 */
export async function getStoredDeviceId(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(DEVICE_ID_KEY);
  } catch (error) {
    console.error('Error getting stored device ID:', error);
    return null;
  }
}

/**
 * Get stored push token
 */
export async function getStoredPushToken(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(PUSH_TOKEN_KEY);
  } catch (error) {
    console.error('Error getting stored push token:', error);
    return null;
  }
}

/**
 * Get device registration status for debugging
 */
export async function getDeviceRegistrationStatus(): Promise<{
  deviceId: string | null;
  pushToken: string | null;
  platform: string;
}> {
  return {
    deviceId: await getStoredDeviceId(),
    pushToken: await getStoredPushToken(),
    platform: Platform.OS === 'ios' ? 'iOS' : 'android',
  };
}

/**
 * Delete device registration from backend when user logs out
 */
export async function deleteDeviceFromBackend(userId: string): Promise<boolean> {
  try {
    console.log(`Starting device deletion for user: ${userId}`);
    
    // Get stored device ID
    const deviceId = await getStoredDeviceId();
    
    if (!deviceId) {
      console.log('No device ID found, nothing to delete');
      return true; // Consider this successful since there's nothing to delete
    }

    console.log(`Deleting device registration: User ${userId}, Device ${deviceId}`);

    // Send deletion request to backend
    const response = await fetch(`${API_URL}/api/device/${userId}/${deviceId}`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Device deletion failed: ${response.status} - ${errorText}`);
      return false;
    }

    const result = await response.json();
    console.log('Device deletion successful:', result.message);
    
    // Optionally clear local storage after successful deletion
    await AsyncStorage.removeItem(DEVICE_ID_KEY);
    await AsyncStorage.removeItem(PUSH_TOKEN_KEY);
    console.log('Local device data cleared');
    
    return true;
  } catch (error) {
    console.error('Error deleting device from backend:', error);
    return false;
  }
} 