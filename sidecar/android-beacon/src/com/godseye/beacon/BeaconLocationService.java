package com.godseye.beacon;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.os.PowerManager;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class BeaconLocationService extends Service implements LocationListener {

    public static final String ACTION_START = "com.godseye.beacon.START";
    public static final String ACTION_STOP = "com.godseye.beacon.STOP";
    private static final String CHANNEL_ID = "sentinel_beacon_channel";
    private static final int NOTIF_ID = 4040;

    private static boolean isRunning = false;
    private static LocationCallback callback = null;

    private LocationManager locationManager;
    private PowerManager.WakeLock wakeLock;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private int pingsSent = 0;
    private long lastPingTime = 0;

    public interface LocationCallback {
        void onLocationUpdate(double lat, double lon, float acc, float speedKts, float heading, double alt, int pings);
        void onServiceStateChanged(boolean running);
    }

    public static void setCallback(LocationCallback cb) {
        callback = cb;
        if (callback != null) {
            callback.onServiceStateChanged(isRunning);
        }
    }

    public static boolean isServiceRunning() {
        return isRunning;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SentinelBeacon:WakeLock");
            wakeLock.setReferenceCounted(false);
        }

        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }

        if (!isRunning) {
            startTracking();
        }

        return START_STICKY;
    }

    private void startTracking() {
        isRunning = true;
        if (wakeLock != null && !wakeLock.isHeld()) {
            wakeLock.acquire(12 * 60 * 60 * 1000L); // Max 12 hours
        }

        Notification notification = buildNotification("Acquiring GPS fix...");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } else {
            startForeground(NOTIF_ID, notification);
        }

        try {
            // Request High-Accuracy GPS updates (1 second, 0.5 meter)
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER, 1000L, 0.5f, this);
            }
            // Also request Network updates as immediate fallback
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER, 2000L, 1.0f, this);
            }
        } catch (SecurityException ignored) {
        }

        if (callback != null) {
            callback.onServiceStateChanged(true);
        }
    }

    private void stopTracking() {
        isRunning = false;
        try {
            if (locationManager != null) {
                locationManager.removeUpdates(this);
            }
        } catch (SecurityException ignored) {
        }

        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }

        stopForeground(true);

        if (callback != null) {
            callback.onServiceStateChanged(false);
        }
    }

    @Override
    public void onDestroy() {
        stopTracking();
        executor.shutdownNow();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // --- LocationListener implementation ---

    @Override
    public void onLocationChanged(Location loc) {
        if (loc == null) return;

        long now = System.currentTimeMillis();
        if (now - lastPingTime < 1000) return; // Cap at 1 ping/sec
        lastPingTime = now;

        final double lat = loc.getLatitude();
        final double lon = loc.getLongitude();
        final float acc = loc.getAccuracy();
        final float speedKts = loc.getSpeed() * 1.94384f;
        final float heading = loc.getBearing();
        final double alt = loc.getAltitude();

        pingsSent++;

        // Update foreground notification
        updateNotification(String.format("Lat: %.5f, Lon: %.5f (±%.1fm, %d pings)", lat, lon, acc, pingsSent));

        // Notify UI
        if (callback != null) {
            callback.onLocationUpdate(lat, lon, acc, speedKts, heading, alt, pingsSent);
        }

        // Transmit HTTP Ping in background
        executor.execute(() -> sendHttpPing(lat, lon, acc, speedKts, heading, alt));
    }

    private void sendHttpPing(double lat, double lon, float acc, float speedKts, float heading, double alt) {
        HttpURLConnection conn = null;
        try {
            String serverUrl = Config.getServerUrl(this);
            String deviceId = Config.getDeviceId(this);
            int bubbleRadius = Config.getBubbleRadius(this);

            URL url = new URL(serverUrl);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setDoOutput(true);

            String json = String.format(
                    "{\"device_id\":\"%s\",\"name\":\"VIP Phone\",\"device_type\":\"person\",\"lat\":%.6f,\"lon\":%.6f,\"alt_m\":%.1f,\"speed_kts\":%.2f,\"heading_deg\":%.1f,\"accuracy_m\":%.1f,\"bubble_radius_km\":%d,\"source\":\"sentinel_android_apk\"}",
                    deviceId, lat, lon, alt, speedKts, heading, acc, bubbleRadius
            );

            byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(bytes.length);

            try (OutputStream os = conn.getOutputStream()) {
                os.write(bytes);
                os.flush();
            }

            conn.getResponseCode(); // Trigger request
        } catch (Exception ignored) {
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {}

    @Override
    public void onProviderEnabled(String provider) {}

    @Override
    public void onProviderDisabled(String provider) {}

    // --- Notification Helpers ---

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.channel_name),
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription(getString(R.string.channel_desc));
            channel.setShowBadge(false);
            channel.enableVibration(false);
            channel.enableLights(false);

            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    private Notification buildNotification(String text) {
        Intent launchIntent = new Intent(this, MainActivity.class);
        launchIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(
                this, 0, launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0)
        );

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }

        builder.setContentTitle("🛡️ Sentinel Escort Beacon")
                .setContentText(text)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentIntent(pi)
                .setOngoing(true);

        return builder.build();
    }

    private void updateNotification(String text) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(NOTIF_ID, buildNotification(text));
        }
    }
}
