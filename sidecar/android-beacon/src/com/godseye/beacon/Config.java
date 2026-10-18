package com.godseye.beacon;

import android.content.Context;
import android.content.SharedPreferences;

public class Config {
    private static final String PREF_NAME = "sentinel_beacon_prefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_DEVICE_ID = "device_id";
    private static final String KEY_RADIUS = "bubble_radius";

    public static final String DEFAULT_URL = "http://192.168.20.120:8080/api/escort/ping";
    public static final String DEFAULT_DEVICE_ID = "VIP-ANDROID-1";
    public static final int DEFAULT_RADIUS = 15;

    public static String getServerUrl(Context ctx) {
        return ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_SERVER_URL, DEFAULT_URL);
    }

    public static void setServerUrl(Context ctx, String url) {
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit().putString(KEY_SERVER_URL, url).apply();
    }

    public static String getDeviceId(Context ctx) {
        return ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_DEVICE_ID, DEFAULT_DEVICE_ID);
    }

    public static void setDeviceId(Context ctx, String id) {
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit().putString(KEY_DEVICE_ID, id).apply();
    }

    public static int getBubbleRadius(Context ctx) {
        return ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getInt(KEY_RADIUS, DEFAULT_RADIUS);
    }

    public static void setBubbleRadius(Context ctx, int radius) {
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit().putInt(KEY_RADIUS, radius).apply();
    }
}
