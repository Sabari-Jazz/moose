// app.config.js

// IMPORTANT: Load environment variables FIRST
// Make sure you have a .env file and `dotenv` installed as a dev dependency
// Or manage environment variables through your CI/CD or EAS Secrets
require('dotenv').config();

export default {
  expo: {
    // --- Basic App Info (Using values from app.config.js primarily) ---
    
    name: "Moose Solar",
    slug: "moose-solar",
    //name: "Moose Solar Dev",
    version: "2.5.2", // Consider using eas.json appVersionSource: remote
    orientation: "portrait",
    scheme: "moose-solar", // Added from app.json
    updates: {
        url: "https://u.expo.dev/f8b79784-8f4b-42a9-aa3c-e8a901abba87"
      },
      runtimeVersion: {
        policy: "appVersion"
      },
      owner: "sabarijazz",
    
    // --- Icon and Splash ---
    // !!! IMPORTANT: You MUST manually edit './assets/icon.png'
    // !!! to make it a square image (e.g., 1024x1024 pixels) for this to work.
    icon: "./assets/icon.png",
    // Using splash from app.config.js (different background color)
    splash: {
      image: "./assets/splash.png",
      contentFit: "contain",
      backgroundColor: "#0066CC", // From app.config.js
    },

    // --- General Settings ---
    userInterfaceStyle: "automatic",
    jsEngine: "hermes",
    assetBundlePatterns: ["**/*"],
    // Enable New Architecture (from app.json) and Typed Routes (from app.config.js)
    newArchEnabled: true, // from app.json
    experiments: {
      tsconfigPaths: true, // from app.json
      typedRoutes: true, // from app.config.js
    },

    // --- iOS Specific ---
    ios: {
      supportsTablet: true,
      bundleIdentifier: "com.moose.solar",
    //  bundleIdentifier: "com.moose.solar.dev",
      buildNumber: "2.5.2", // Consider using eas build --auto-increment or remote app version source
      // Merged infoPlist entries from both files
      infoPlist: {
        // From app.json
        NSCameraUsageDescription: "This app uses the camera to scan QR codes.",
        NSLocationWhenInUseUsageDescription: "This app uses your location to show nearby solar systems.", // Present in both, keep one
        // From app.config.js
        UIStatusBarStyle: "UIStatusBarStyleDarkContent", // Present in both, keep one
        UIViewControllerBasedStatusBarAppearance: false, // Present in both, keep one
        ITSAppUsesNonExemptEncryption: false,
        NSUserNotificationUsageDescription: "We use notifications to send you daily reminders and updates about your solar system.",

      },
      // Note: googleMapsApiKey removed, handled via 'extra' below
    },

    // --- Android Specific ---
    android: {
      // !!! IMPORTANT: Ensure './assets/icon.png' is square !!!
      adaptiveIcon: {
        foregroundImage: "./assets/icon.png",
        backgroundColor: "#0066CC", // From app.config.js
      },
      package: "com.moose.solar",
      googleServicesFile: "./google-services.json",
    //  versionCode: 1, // Consider using eas build --auto-increment or remote app version source
      // Merged permissions from both files
      permissions: [
        // Common
        "CAMERA",
        "ACCESS_FINE_LOCATION",
        // From app.json
        "INTERNET",
        "ACCESS_NETWORK_STATE",
        // From app.config.js
        "RECEIVE_BOOT_COMPLETED",
        "VIBRATE",
        "SCHEDULE_EXACT_ALARM",
      ],
      // Correct way to handle keyboard behavior (from app.config.js)
      softwareKeyboardLayoutMode: "pan",
      // Note: googleMaps.apiKey and incorrect windowSoftInputMode removed
      buildNumber: "2.5.2",
      config: {
        googleMaps: {
          apiKey: process.env.GOOGLE_MAPS_API_KEY,
        },
      },
    },

    // --- Web Specific ---
    web: {
      // Using icon.png from app.config.js (app.json used favicon.png)
      favicon: "./assets/icon.png",
      bundler: "metro",
    },

    // --- Plugins (Merged List) ---
    // Review this list carefully to ensure you need all of them!
    plugins: [
      "expo-router", // Common plugin
      // From app.json
      [
        "expo-location",
        {
          locationAlwaysAndWhenInUsePermission: "Allow $(PRODUCT_NAME) to use your location.",
        },
      ],
      [
        "expo-image-picker",
        {
          photosPermission: "The app accesses your photos to let you share them with your friends.",
        },
      ],
      [
        "react-native-background-fetch",
        {
          minimumInterval: 60,
        },
      ],
      // "@react-native-community/netinfo", // Bare modules might need extra setup/checking
      // "@react-native-async-storage/async-storage", // Bare modules might need extra setup/checking
      // // From app.config.js
      // [
      //   "expo-notifications",
      //   {
      //     // !!! IMPORTANT: Ensure these asset files exist and are correct !!!
      //     icon: "./assets/notification-icon.png", // Make sure this icon exists
      //     color: "#0066CC",
      //     sounds: ["./assets/notification-sound.wav"], // Make sure this sound file exists
      //   },
      // ],
    ],

    // --- Extra / Environment Variables (From app.config.js) ---
    extra: {
      // Expose environment variables to the app
      // Ensure the .env file is correctly loaded above or variables are set via EAS Secrets / CI
      googleMapsApiKey: process.env.GOOGLE_MAPS_API_KEY,
      solarWebAccessKeyId: process.env.SOLAR_WEB_ACCESS_KEY_ID,
      solarWebAccessKeyValue: process.env.SOLAR_WEB_ACCESS_KEY_VALUE,
      solarWebUserId: process.env.SOLAR_WEB_USERID,
      solarWebPassword: process.env.SOLAR_WEB_PASSWORD,
      apiBaseUrl: process.env.API_BASE_URL,
      supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL2,
      supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY2,
      awsApiUrl: process.env.AWS_API_URL,
      // EAS Project ID (using the one from app.config.js)
    /*
      eas: {
       projectId: "a6e0a3fc-5475-4e55-819f-8257b0ea3fb3",
    },
    */
      eas: {
        projectId: "f8b79784-8f4b-42a9-aa3c-e8a901abba87"
      },
      // Added from app.json's extra field (if needed, otherwise remove)
      router: {
        origin: false,
      },
    },
  },
};