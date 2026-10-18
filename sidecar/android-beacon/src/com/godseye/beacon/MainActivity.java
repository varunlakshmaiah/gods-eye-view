package com.godseye.beacon;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity implements BeaconLocationService.LocationCallback {

    private static final int PERMISSION_REQ_CODE = 1001;

    private TextView tvStatusBadge;
    private Button btnToggle;
    private TextView tvPingCount;
    private TextView tvCoords;
    private TextView tvAccuracy;
    private TextView tvSpeed;
    private TextView tvHeading;
    private TextView tvAlt;
    private EditText etServerUrl;
    private EditText etDeviceId;
    private EditText etBubbleRadius;
    private Button btnBattery;
    private TextView tvBatteryStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        bindViews();
        setupConfigInputs();
        setupListeners();

        // Check permissions and auto-start if ready
        if (checkLocationPermissions()) {
            autoStartTracking();
        } else {
            requestAppPermissions();
        }
    }

    private void bindViews() {
        tvStatusBadge = findViewById(R.id.tv_status_badge);
        btnToggle = findViewById(R.id.btn_toggle);
        tvPingCount = findViewById(R.id.tv_ping_count);
        tvCoords = findViewById(R.id.tv_coords);
        tvAccuracy = findViewById(R.id.tv_accuracy);
        tvSpeed = findViewById(R.id.tv_speed);
        tvHeading = findViewById(R.id.tv_heading);
        tvAlt = findViewById(R.id.tv_alt);
        etServerUrl = findViewById(R.id.et_server_url);
        etDeviceId = findViewById(R.id.et_device_id);
        etBubbleRadius = findViewById(R.id.et_bubble_radius);
        btnBattery = findViewById(R.id.btn_battery);
        tvBatteryStatus = findViewById(R.id.tv_battery_status);
    }

    private void setupConfigInputs() {
        etServerUrl.setText(Config.getServerUrl(this));
        etDeviceId.setText(Config.getDeviceId(this));
        etBubbleRadius.setText(String.valueOf(Config.getBubbleRadius(this)));

        etServerUrl.addTextChangedListener(new SimpleTextWatcher() {
            @Override
            public void afterTextChanged(Editable s) {
                if (s != null && s.length() > 0) {
                    Config.setServerUrl(MainActivity.this, s.toString().trim());
                }
            }
        });

        etDeviceId.addTextChangedListener(new SimpleTextWatcher() {
            @Override
            public void afterTextChanged(Editable s) {
                if (s != null && s.length() > 0) {
                    Config.setDeviceId(MainActivity.this, s.toString().trim());
                }
            }
        });

        etBubbleRadius.addTextChangedListener(new SimpleTextWatcher() {
            @Override
            public void afterTextChanged(Editable s) {
                try {
                    int val = Integer.parseInt(s.toString().trim());
                    if (val > 0) Config.setBubbleRadius(MainActivity.this, val);
                } catch (Exception ignored) {}
            }
        });
    }

    private void setupListeners() {
        btnToggle.setOnClickListener(v -> {
            if (BeaconLocationService.isServiceRunning()) {
                stopTrackingService();
            } else {
                if (checkLocationPermissions()) {
                    startTrackingService();
                } else {
                    requestAppPermissions();
                }
            }
        });

        btnBattery.setOnClickListener(v -> requestIgnoreBatteryOptimizations());
    }

    @Override
    protected void onResume() {
        super.onResume();
        BeaconLocationService.setCallback(this);
        updateServiceUI(BeaconLocationService.isServiceRunning());
        updateBatteryOptimizationUI();
    }

    @Override
    protected void onPause() {
        BeaconLocationService.setCallback(null);
        super.onPause();
    }

    private void autoStartTracking() {
        if (!BeaconLocationService.isServiceRunning()) {
            startTrackingService();
        }
    }

    private void startTrackingService() {
        Intent intent = new Intent(this, BeaconLocationService.class);
        intent.setAction(BeaconLocationService.ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        updateServiceUI(true);
    }

    private void stopTrackingService() {
        Intent intent = new Intent(this, BeaconLocationService.class);
        intent.setAction(BeaconLocationService.ACTION_STOP);
        startService(intent);
        updateServiceUI(false);
    }

    private void updateServiceUI(boolean running) {
        if (running) {
            tvStatusBadge.setText("TRANSMITTING");
            tvStatusBadge.setTextColor(0xFF00FF88);
            tvStatusBadge.setBackgroundColor(0x2200FF88);

            btnToggle.setText("⏹️ STOP TRANSMITTING");
            btnToggle.setBackgroundColor(0xFFFF3366);
            btnToggle.setTextColor(0xFFFFFFFF);
        } else {
            tvStatusBadge.setText("OFFLINE");
            tvStatusBadge.setTextColor(0xFFFF3366);
            tvStatusBadge.setBackgroundColor(0x22FF3366);

            btnToggle.setText("📡 START TRANSMITTING");
            btnToggle.setBackgroundColor(0xFF00FF88);
            btnToggle.setTextColor(0xFF000000);
        }
    }

    // --- Battery Optimizations ---

    private void updateBatteryOptimizationUI() {
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            boolean ignoring = pm.isIgnoringBatteryOptimizations(getPackageName());
            if (ignoring) {
                tvBatteryStatus.setText("⚡ Background Battery: UNRESTRICTED (Safe for 24/7 background)");
                tvBatteryStatus.setTextColor(0xFF00FF88);
                btnBattery.setVisibility(View.GONE);
            } else {
                tvBatteryStatus.setText("⚠️ Battery Optimization Active: Tap below to allow 24/7 streaming");
                tvBatteryStatus.setTextColor(0xFFFFCC00);
                btnBattery.setVisibility(View.VISIBLE);
            }
        }
    }

    private void requestIgnoreBatteryOptimizations() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                try {
                    Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    intent.setData(Uri.parse("package:" + getPackageName()));
                    startActivity(intent);
                } catch (Exception e) {
                    Intent intent = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                    startActivity(intent);
                }
            }
        }
    }

    // --- Permissions ---

    private boolean checkLocationPermissions() {
        boolean fine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean coarse = checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        return fine && coarse;
    }

    private void requestAppPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION,
                    Manifest.permission.POST_NOTIFICATIONS
            }, PERMISSION_REQ_CODE);
        } else {
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
            }, PERMISSION_REQ_CODE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQ_CODE) {
            if (checkLocationPermissions()) {
                // Request background location on Android 10+ if needed
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    if (checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) != PackageManager.PERMISSION_GRANTED) {
                        requestPermissions(new String[]{Manifest.permission.ACCESS_BACKGROUND_LOCATION}, 1002);
                    }
                }
                autoStartTracking();
            } else {
                Toast.makeText(this, "Location permission is required for escort tracking", Toast.LENGTH_LONG).show();
            }
        }
    }

    // --- Location Callback ---

    @Override
    public void onLocationUpdate(double lat, double lon, float acc, float speedKts, float heading, double alt, int pings) {
        runOnUiThread(() -> {
            tvCoords.setText(String.format("%.6f, %.6f", lat, lon));
            tvAccuracy.setText(String.format("± %.1f m", acc));
            tvAccuracy.setTextColor(acc <= 10 ? 0xFF00FF88 : (acc <= 30 ? 0xFFFFCC00 : 0xFFFF3366));
            tvSpeed.setText(String.format("%.1f kts (%.1f km/h)", speedKts, speedKts * 1.852f));
            tvHeading.setText(heading > 0 ? String.format("%d°", Math.round(heading)) : "--°");
            tvAlt.setText(String.format("%.0f m", alt));
            tvPingCount.setText(String.format("%d PINGS", pings));
        });
    }

    @Override
    public void onServiceStateChanged(boolean running) {
        runOnUiThread(() -> updateServiceUI(running));
    }

    private abstract static class SimpleTextWatcher implements TextWatcher {
        @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
        @Override public void onTextChanged(CharSequence s, int start, int before, int count) {}
    }
}
