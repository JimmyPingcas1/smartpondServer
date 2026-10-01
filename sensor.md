// ======================================================
// SMARTPOND ESP32 — Auto Control + Live Sensors + Warnings
// ======================================================
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

// ======================================================
// ▼▼▼  USER CONFIGURATION — EDIT ONLY THIS SECTION  ▼▼▼
// ======================================================
namespace CFG {

  // ---- Wi-Fi ----
  const char* WIFI_SSID     = "";
  const char* WIFI_PASSWORD = "1";

  // ---- Account / Pond ----
  const char* USER_ID = "69a39acc56b522b28deec4a9";
  const char* POND_ID = "69a39f9cbb28dfc1b9a307fb";

 // ---- Render server ----
  const char* HOST = "smartpond-server.onrender.com";
  const uint16_t PORT = 443;

  // ---- Timing (ms) ----
  const unsigned long WIFI_RECONNECT_INTERVAL = 5000;
  const unsigned long LIVE_SENSOR_INTERVAL    = 5000;
  const unsigned long SENSOR_RECORD_INTERVAL  = 20UL * 60UL * 1000UL; // 20 min

  // ---- Relay pins (active LOW) ----
  const int PIN_PUMP_1  = 22;
  const int PIN_PUMP_2  = 23;
  const int PIN_AERATOR = 19;
  const int PIN_HEATER  = 21;

  // ---- Sensor pins ----
  const int PIN_TEMP      = 32;
  const int PIN_PH        = 33;
  const int PIN_TURBIDITY = 35;
  const int PIN_MQ        = 34;

  // ---- Water quality safe ranges (used for warnings) ----
  const float TEMP_MIN = 25.0,  TEMP_MAX = 30.0;
  const float PH_MIN   = 6.5,   PH_MAX   = 7.5;
  const float NTU_MIN  = 10.0,  NTU_MAX  = 50.0;
  const float AMMONIA_MAX = 0.02;

  // ---- Automation thresholds (with buffer/hysteresis) ----
  const float HEATER_ON_BELOW   = 25.0;   // heater ON  if temp < 25
  const float HEATER_OFF_ABOVE  = 26.0;   // heater OFF if temp >= 26
  const float PUMP_ON_ABOVE_NTU = 50.0;   // pump ON   if turbidity > 50
  const float PUMP_OFF_BELOW_NTU= 45.0;   // pump OFF  if turbidity <= 45
  const float PUMP_ON_ABOVE_NH3 = 0.02;   // pump ON   if ammonia > 0.02
  const float PUMP_OFF_BELOW_NH3= 0.017;  // pump OFF  if ammonia <= 0.017

} // namespace CFG

// ======================================================
// SHORTCUTS
// ======================================================
#define RELAY_ON  LOW
#define RELAY_OFF HIGH
#define VREF      3.3
#define ADC_RES   4095.0

// ======================================================
// WEBSOCKETS
// ======================================================
WebSocketsClient webSocket;          // auto-control
WebSocketsClient sensorWebSocket;    // live sensor data
bool wsConnected       = false;
bool sensorWsConnected = false;

// ======================================================
// STATE
// ======================================================
bool aeratorOn    = false;
bool waterpumpOn  = false;
bool heaterOn     = false;
bool automationOn = false;

// Pending automation POSTs, one entry per device
const char* pendingDevices[] = {"aerator", "waterpump", "heater"};
bool pendingAutomationPost[3] = {false, false, false};
bool pendingState[3] = {false, false, false};
unsigned long pendingPostTime[3] = {0, 0, 0};

// Sensors
float temperature = 0, phLevel = 0, turbidity = 0, ammonia = 0;
bool  tempValid = false, phValid = false,
      turbidityValid = false, ammoniaValid = false;

// Error tracking
bool prevTempValid = true, prevPhValid = true,
     prevTurbidityValid = true, prevAmmoniaValid = true;
bool sensorErrorActive = false;

// Water quality warning tracking
bool temperatureWarningActive = false;
bool phWarningActive = false;
bool turbidityWarningActive = false;
bool ammoniaWarningActive = false;

// Smoothing
float smoothPH = 0, smoothNTU = 0;

// Timers
unsigned long lastWiFiReconnectAttempt = 0;
unsigned long lastLiveSensorSend       = 0;
unsigned long lastSensorRecord         = 0;
bool          firstSensorRecord        = true;
bool          wasWiFiConnected         = false;

// DS18B20
OneWire           oneWire(CFG::PIN_TEMP);
DallasTemperature ds18b20(&oneWire);

// URLs
String automationDeviceStateUrl =
    "https://" + String(CFG::HOST) + "/api/v1/AutomationDeviceState";
String warningsUrl =
    "https://" + String(CFG::HOST) + "/api/v1/warnings";
String sensorAutoUrl =
    "https://" + String(CFG::HOST) + "/api/v1/sensor-auto";

// ======================================================
// FORWARD DECLARATIONS
// ======================================================
void setDeviceRelay(const char* device, bool state, bool fromAutomation);
bool postAutomationDeviceState(const char* device, bool state);

// ======================================================
// SENSOR READERS
// ======================================================
float readPH() {
  long total = 0;
  for (int i = 0; i < 20; i++) { total += analogRead(CFG::PIN_PH); delay(5); }
  int raw = total / 20;
  if (raw <= 50 || raw >= 4000 || (raw >= 600 && raw <= 660)) return 0.0;

  float v = (raw / 4095.0) * 3.3;
  float targetPH = 7.0;

  if (v >= 1.847) {
    if (v >= 2.500)
      targetPH = 6.86 - ((v - 1.847) * ((6.86 - 4.01) / (2.928 - 1.847)));
    else
      targetPH = 6.86 + ((v - 1.847) * ((9.18 - 6.86) / (2.284 - 1.847)));
  } else {
    targetPH = 6.86;
  }
  targetPH = constrain(targetPH, 0.0, 14.0);

  if (smoothPH == 0.0) smoothPH = targetPH;
  smoothPH = 0.85 * smoothPH + 0.15 * targetPH;
  return smoothPH;
}

float readTurbidity() {
  long total = 0;
  for (int i = 0; i < 20; i++) { total += analogRead(CFG::PIN_TURBIDITY); delay(5); }
  int raw = total / 20;
  if (raw <= 50 || raw >= 3900) return 0.0;

  float v = (raw / 4095.0) * 3.3;
  float ntu = 0.0;

  if (v >= 0.965) {
    ntu = (1.011 - v) * (20.0 / (1.011 - 0.965));
    if (ntu < 0.0) ntu = 0.0;
  } else if (v >= 0.750) {
    ntu = 20.0 + (0.965 - v) * (20.0 / (0.965 - 0.750));
  } else if (v >= 0.500) {
    ntu = 40.0 + (0.750 - v) * (10.0 / (0.750 - 0.500));
  } else {
    ntu = 50.0 + (0.500 - v) * (25.0 / (0.500 - 0.430));
    if (ntu > 75.0) ntu = 75.0;
  }
  ntu = constrain(ntu, 0.0, 75.0);

  if (smoothNTU == 0.0) smoothNTU = ntu;
  smoothNTU = 0.88 * smoothNTU + 0.12 * ntu;
  return smoothNTU;
}

float readAmmonia() {
  long total = 0;
  for (int i = 0; i < 30; i++) { total += analogRead(CFG::PIN_MQ); delay(5); }
  int raw = total / 30;
  if (raw <= 50 || raw >= 4000) return -1.0;

  float v = (raw / 4095.0) * 3.3;
  float ppm = (v > 0.1860) ? 0.0218818 * (v - 0.1860) : 0.0;
  return constrain(ppm, 0.0, 2.0);
}

bool readSensors() {
  phLevel = readPH();
  phValid = (phLevel > 0.0 && phLevel <= 14.0);

  turbidity = readTurbidity();
  turbidityValid = (turbidity > 0.0 && turbidity <= 75.0);

  ds18b20.requestTemperatures();
  float t = ds18b20.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C || t < -50.0 || t == 85.0) {
    tempValid = false;
  } else {
    tempValid = true;
    temperature = t;
  }

  ammonia = readAmmonia();
  ammoniaValid = (ammonia >= 0.0);

  Serial.println("================================");
  Serial.println("[SENSOR STATUS]");
  Serial.printf("Temperature: %s", tempValid ? "OK" : "FAILED");
  if (tempValid) Serial.printf(" (%.2f C)", temperature);
  Serial.println();
  Serial.printf("pH: %s", phValid ? "OK" : "FAILED");
  if (phValid) Serial.printf(" (%.2f)", phLevel);
  Serial.println();
  Serial.printf("Turbidity: %s", turbidityValid ? "OK" : "FAILED");
  if (turbidityValid) Serial.printf(" (%.2f NTU)", turbidity);
  Serial.println();
  Serial.printf("Ammonia: %s", ammoniaValid ? "OK" : "FAILED");
  if (ammoniaValid) Serial.printf(" (%.4f PPM)", ammonia);
  Serial.println();
  Serial.println("================================");

  return (tempValid && phValid && turbidityValid && ammoniaValid);
}

// ======================================================
// RELAY CONTROL
// ======================================================
void setDeviceRelay(const char* device, bool state, bool fromAutomation = false) {
  bool oldState;
  if      (strcmp(device, "waterpump") == 0) oldState = waterpumpOn;
  else if (strcmp(device, "aerator")   == 0) oldState = aeratorOn;
  else if (strcmp(device, "heater")    == 0) oldState = heaterOn;
  else { Serial.printf("UNKNOWN DEVICE: %s\n", device); return; }

  if (oldState == state) return;

  if (strcmp(device, "waterpump") == 0) {
    waterpumpOn = state;
    digitalWrite(CFG::PIN_PUMP_1, state ? RELAY_ON : RELAY_OFF);
    digitalWrite(CFG::PIN_PUMP_2, state ? RELAY_ON : RELAY_OFF);
  } else if (strcmp(device, "aerator") == 0) {
    aeratorOn = state;
    digitalWrite(CFG::PIN_AERATOR, state ? RELAY_ON : RELAY_OFF);
  } else if (strcmp(device, "heater") == 0) {
    heaterOn = state;
    digitalWrite(CFG::PIN_HEATER, state ? RELAY_ON : RELAY_OFF);
  }
  Serial.printf("%s: %s\n",
                strcmp(device,"waterpump")==0 ? "WATER PUMP" :
                strcmp(device,"aerator")  ==0 ? "AERATOR"    : "HEATER",
                state ? "ON" : "OFF");

  if (fromAutomation && automationOn) {
    int deviceIndex = -1;
    if (strcmp(device, "aerator") == 0) deviceIndex = 0;
    else if (strcmp(device, "waterpump") == 0) deviceIndex = 1;
    else if (strcmp(device, "heater") == 0) deviceIndex = 2;

    if (deviceIndex >= 0) {
      pendingAutomationPost[deviceIndex] = true;
      pendingState[deviceIndex] = state;
      pendingPostTime[deviceIndex] = millis();
    }
    Serial.printf("[AUTO] Queued DB update: %s -> %s\n", device, state ? "ON" : "OFF");
  }
}

void stopAutomationDevices() {
  Serial.println("[SAFETY] Stopping automation devices (pump + heater only)");
  setDeviceRelay("waterpump", false, false);
  setDeviceRelay("heater",    false, false);
  waterpumpOn = false;
  heaterOn    = false;
}

// ======================================================
// HTTP POST HELPERS
// ======================================================
bool httpPostJSON(const String& url, const String& body, const char* tag) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[%s] WiFi unavailable.\n", tag);
    return false;
  }
  WiFiClientSecure client;
  client.setInsecure();  // TESTING ONLY
  HTTPClient http;

  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");
  http.setConnectTimeout(3000);   // shortened
  http.setTimeout(5000);          // shortened

  Serial.printf("\n================================\n[%s]\n================================\n", tag);
  Serial.println(body);

  int code = http.POST(body);
  Serial.printf("[%s] HTTP Code: %d\n", tag, code);

  if (code > 0) {
    String resp = http.getString();
    Serial.printf("[%s] Server response:\n%s\n", tag, resp.c_str());
  }
  http.end();
  return (code >= 200 && code < 300);
}

String warningsEndpoint() {
  return warningsUrl +
         "?user_id=" + String(CFG::USER_ID) +
         "&pond_id=" + String(CFG::POND_ID);
}

// ======================================================
// SENSOR ERROR / RECOVERY
// ======================================================
bool sendSensorErrorToServer() {
  StaticJsonDocument<1024> doc;

  doc["status"]        = "sensor_error";
  doc["issue_type"]    = "sensor_error";
  doc["sensor_status"] = false;

  JsonObject s = doc.createNestedObject("sensors");
  s["temperature"] = tempValid      ? temperature : 0.0;
  s["ph"]          = phValid        ? phLevel     : 0.0;
  s["turbidity"]   = turbidityValid ? turbidity   : 0.0;
  s["ammonia"]     = ammoniaValid   ? ammonia     : 0.0;

  JsonArray errs = doc.createNestedArray("sensor_errors");
  if (!tempValid)      errs.add("temperature");
  if (!phValid)        errs.add("ph");
  if (!turbidityValid) errs.add("turbidity");
  if (!ammoniaValid)   errs.add("ammonia");

  String body; serializeJson(doc, body);
  return httpPostJSON(warningsEndpoint(), body, "SENSOR ERROR");
}

bool sendSensorRecoveryToServer() {
  StaticJsonDocument<1024> doc;
  doc["status"]        = "fixed";
  doc["issue_type"]    = "sensor_error";
  doc["sensor_status"] = true;
  doc["message"]       = "All sensors are working again.";

  JsonObject s = doc.createNestedObject("sensors");
  s["temperature"] = tempValid      ? temperature : 0.0;
  s["ph"]          = phValid        ? phLevel     : 0.0;
  s["turbidity"]   = turbidityValid ? turbidity   : 0.0;
  s["ammonia"]     = ammoniaValid   ? ammonia     : 0.0;

  String body; serializeJson(doc, body);
  return httpPostJSON(warningsEndpoint(), body, "SENSOR RECOVERY");
}

// ======================================================
// WATER QUALITY WARNING / FIXED
// ======================================================
bool sendWaterQualityWarning(const char* sensor, float value, const char* message) {
  StaticJsonDocument<768> doc;
  doc["status"]        = "warning";
  doc["issue_type"]    = "water_quality";
  doc["sensor"]        = sensor;
  doc["value"]         = value;
  doc["message"]       = message;
  doc["sensor_status"] = true;

  JsonObject s = doc.createNestedObject("sensors");
  s["temperature"] = tempValid      ? temperature : 0.0;
  s["ph"]          = phValid        ? phLevel     : 0.0;
  s["turbidity"]   = turbidityValid ? turbidity   : 0.0;
  s["ammonia"]     = ammoniaValid   ? ammonia     : 0.0;

  String body; serializeJson(doc, body);
  return httpPostJSON(warningsEndpoint(), body, "WATER QUALITY WARNING");
}

bool sendWaterQualityFixed(const char* sensor, float value, const char* message) {
  StaticJsonDocument<768> doc;
  doc["status"]        = "fixed";
  doc["issue_type"]    = "water_quality";
  doc["sensor"]        = sensor;
  doc["value"]         = value;
  doc["message"]       = message;
  doc["sensor_status"] = true;

  JsonObject s = doc.createNestedObject("sensors");
  s["temperature"] = tempValid      ? temperature : 0.0;
  s["ph"]          = phValid        ? phLevel     : 0.0;
  s["turbidity"]   = turbidityValid ? turbidity   : 0.0;
  s["ammonia"]     = ammoniaValid   ? ammonia     : 0.0;

  String body; serializeJson(doc, body);
  return httpPostJSON(warningsEndpoint(), body, "WATER QUALITY FIXED");
}

// ======================================================
// AUTOMATION STATE POST (queued)
// ======================================================
bool postAutomationDeviceState(const char* device, bool state) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;

  String url = automationDeviceStateUrl +
               "?user_id=" + String(CFG::USER_ID) +
               "&pond_id=" + String(CFG::POND_ID) +
               "&device="  + String(device) +
               "&action="  + String(state ? "ON" : "OFF");

  Serial.printf("[AUTO] HTTPS POST: %s\n", url.c_str());
  http.begin(client, url);
  http.setConnectTimeout(2000);
  http.setTimeout(2000);
  int code = http.POST("");
  Serial.printf("[AUTO] POST %s: %s | Code: %d\n",
                device, state ? "ON" : "OFF", code);
  http.end();
  return (code >= 200 && code < 300);
}

void processPendingAutomationPost() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  for (int deviceIndex = 0; deviceIndex < 3; deviceIndex++) {
    if (!pendingAutomationPost[deviceIndex]) continue;
    if (millis() - pendingPostTime[deviceIndex] < 100) continue;

    const char* device = pendingDevices[deviceIndex];
    bool state = pendingState[deviceIndex];

    Serial.printf("[AUTO] Processing DB update: %s -> %s\n",
                  device, state ? "ON" : "OFF");

    bool success = postAutomationDeviceState(device, state);

    if (success) {
      pendingAutomationPost[deviceIndex] = false;
      Serial.println("[AUTO] DB update successful.");
    } else {
      Serial.println("[AUTO] DB update failed - keeping POST pending.");
    }

    // Send at most one request per loop iteration.
    break;
  }
}

// ======================================================
// SENSOR DATA RECORDING (20 min)
// ======================================================
bool postSensorData() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[RECORD] WiFi unavailable - sensor record skipped.");
    return false;
  }
  if (!tempValid || !phValid || !turbidityValid || !ammoniaValid) {
    Serial.println("[RECORD] Sensor data invalid - record skipped.");
    return false;
  }

  StaticJsonDocument<512> doc;
  doc["temperature"] = temperature;
  doc["turbidity"]   = turbidity;
  doc["ph"]          = phLevel;
  doc["ammonia"]     = ammonia;

  String url = sensorAutoUrl +
               "?user_id=" + String(CFG::USER_ID) +
               "&pond_id=" + String(CFG::POND_ID);

  String body; serializeJson(doc, body);
  return httpPostJSON(url, body, "RECORD");
}

// ======================================================
// LIVE SENSOR (WebSocket, 5 s)
// ======================================================
void sendLiveSensorData() {
  if (!sensorWsConnected) {
    Serial.println("[LIVE WS] Not connected - sensor data not sent.");
    return;
  }
  if (!tempValid || !phValid || !turbidityValid || !ammoniaValid) {
    Serial.println("[LIVE WS] Invalid sensor data - not sent.");
    return;
  }

  StaticJsonDocument<512> doc;
  doc["type"]        = "sensor_data";
  doc["temperature"] = temperature;
  doc["ph"]          = phLevel;
  doc["turbidity"]   = turbidity;
  doc["ammonia"]     = ammonia;

  String json; serializeJson(doc, json);
  Serial.println("\n================================\n[LIVE WS] SENDING SENSOR DATA\n================================\n");
  Serial.println(json);
  sensorWebSocket.sendTXT(json);
}

// ======================================================
// CHECKS
// ======================================================
void checkSensorErrors() {
  bool anyFail = !tempValid || !phValid || !turbidityValid || !ammoniaValid;

  if (!anyFail) {
    if (sensorErrorActive) {
      Serial.println("\n================================\n[SENSOR RECOVERY]\nAll sensors are working again.\n================================");
      sendSensorRecoveryToServer();
      sensorErrorActive = false;
    }
    prevTempValid = tempValid; prevPhValid = phValid;
    prevTurbidityValid = turbidityValid; prevAmmoniaValid = ammoniaValid;
    return;
  }

  bool newFail =
      (prevTempValid      && !tempValid)      ||
      (prevPhValid        && !phValid)        ||
      (prevTurbidityValid && !turbidityValid) ||
      (prevAmmoniaValid   && !ammoniaValid);

  if (newFail || !sensorErrorActive) {
    Serial.println("\n================================\n[SENSOR FAILURE DETECTED]\n================================");
    if (!tempValid)      Serial.println("Temperature sensor: FAILED");
    if (!phValid)        Serial.println("pH sensor: FAILED");
    if (!turbidityValid) Serial.println("Turbidity sensor: FAILED");
    if (!ammoniaValid)   Serial.println("Ammonia sensor: FAILED");
    sendSensorErrorToServer();
    sensorErrorActive = true;
  }
  prevTempValid = tempValid; prevPhValid = phValid;
  prevTurbidityValid = turbidityValid; prevAmmoniaValid = ammoniaValid;
}

void checkWaterQualityWarnings() {

  // Temperature
  if (tempValid) {
    bool bad = (temperature < CFG::TEMP_MIN || temperature > CFG::TEMP_MAX);
    if (bad && !temperatureWarningActive) {
      sendWaterQualityWarning("temperature", temperature,
        "Temperature is outside the safe range of 25-30 C.");
      temperatureWarningActive = true;
    } else if (!bad && temperatureWarningActive) {
      sendWaterQualityFixed("temperature", temperature,
        "Temperature returned to the safe range.");
      temperatureWarningActive = false;
    }
  }

  // pH
  if (phValid) {
    bool bad = (phLevel < CFG::PH_MIN || phLevel > CFG::PH_MAX);
    if (bad && !phWarningActive) {
      sendWaterQualityWarning("ph", phLevel,
        "pH is outside the safe range of 6.5-7.5.");
      phWarningActive = true;
    } else if (!bad && phWarningActive) {
      sendWaterQualityFixed("ph", phLevel,
        "pH returned to the safe range.");
      phWarningActive = false;
    }
  }

  // Turbidity
  if (turbidityValid) {
    bool bad = (turbidity < CFG::NTU_MIN || turbidity > CFG::NTU_MAX);
    if (bad && !turbidityWarningActive) {
      sendWaterQualityWarning("turbidity", turbidity,
        "Turbidity is outside the safe range of 10-50 NTU.");
      turbidityWarningActive = true;
    } else if (!bad && turbidityWarningActive) {
      sendWaterQualityFixed("turbidity", turbidity,
        "Turbidity returned to the safe range.");
      turbidityWarningActive = false;
    }
  }

  // Ammonia
  if (ammoniaValid) {
    bool bad = (ammonia > CFG::AMMONIA_MAX);
    if (bad && !ammoniaWarningActive) {
      sendWaterQualityWarning("ammonia", ammonia,
        "Ammonia is above the safe limit of 0.02 PPM.");
      ammoniaWarningActive = true;
    } else if (!bad && ammoniaWarningActive) {
      sendWaterQualityFixed("ammonia", ammonia,
        "Ammonia returned to the safe range.");
      ammoniaWarningActive = false;
    }
  }
}

// ======================================================
// AUTOMATION
// ======================================================
void runAutomationRules() {
  if (!automationOn) return;

  if (WiFi.status() != WL_CONNECTED || !wsConnected) {
    Serial.println("[SAFETY] Server/WiFi offline - automation paused.");
    setDeviceRelay("waterpump", false, false);
    setDeviceRelay("heater",    false, false);
    waterpumpOn = false;
    heaterOn    = false;
    return;
  }

  // =========================
  // HEATER - TEMPERATURE
  // =========================
  if (temperature < CFG::HEATER_ON_BELOW) {
    setDeviceRelay("heater", true, true);
  }
  else if (temperature >= CFG::HEATER_OFF_ABOVE) {
    setDeviceRelay("heater", false, true);
  }

  // =========================
  // WATER PUMP - TURBIDITY / AMMONIA
  // =========================
  if (turbidity > CFG::PUMP_ON_ABOVE_NTU || ammonia > CFG::PUMP_ON_ABOVE_NH3) {
    setDeviceRelay("waterpump", true, true);
  }
  else if (turbidity <= CFG::PUMP_OFF_BELOW_NTU && ammonia <= CFG::PUMP_OFF_BELOW_NH3) {
    setDeviceRelay("waterpump", false, true);
  }
}

void applyDeviceStates(JsonObject devices) {
  if (devices.containsKey("aerator"))
    setDeviceRelay("aerator", devices["aerator"].as<bool>(), false);

  if (!automationOn) {
    if (devices.containsKey("waterpump"))
      setDeviceRelay("waterpump", devices["waterpump"].as<bool>(), false);
    if (devices.containsKey("heater"))
      setDeviceRelay("heater", devices["heater"].as<bool>(), false);
  }

  Serial.printf("SYNC: A:%s P:%s H:%s Auto:%s\n",
                aeratorOn ? "ON" : "OFF",
                waterpumpOn ? "ON" : "OFF",
                heaterOn ? "ON" : "OFF",
                automationOn ? "ON" : "OFF");
}

// ======================================================
// WEBSOCKET HANDLERS
// ======================================================
void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] DISCONNECTED");
      wsConnected = false;
      break;

    case WStype_CONNECTED:
      Serial.println("[WS] CONNECTED");
      wsConnected = true;
      break;

    case WStype_TEXT: {
      StaticJsonDocument<1024> doc;
      if (deserializeJson(doc, payload, length)) {
        Serial.println("[WS] JSON ERROR");
        break;
      }

      const char* t = doc["type"] | "";

      if (strcmp(t, "automation_state") == 0 || strcmp(t, "automation") == 0) {
        bool prev = automationOn;
        automationOn = doc["automation"] | false;
        Serial.printf("[AUTO] State: %s -> %s\n",
                      prev ? "ON" : "OFF", automationOn ? "ON" : "OFF");
        if (doc.containsKey("devices"))
          applyDeviceStates(doc["devices"].as<JsonObject>());
      }
      else if (strcmp(t, "device_control") == 0) {
        const char* device = doc["device"] | "";
        const char* action = doc["action"] | "";
        bool state = strcmp(action, "ON") == 0;

        if (automationOn) {
          if (strcmp(device, "aerator") == 0) {
            setDeviceRelay(device, state, false);
          } else if (strcmp(device, "heater") == 0 || strcmp(device, "waterpump") == 0) {
            Serial.printf("[CTRL] Auto ON - Ignoring manual command for %s\n", device);
            return;
          }
        } else {
          setDeviceRelay(device, state, false);
        }

        if (doc.containsKey("devices"))
          applyDeviceStates(doc["devices"].as<JsonObject>());
      }
      else if (strcmp(t, "pong") == 0) {
        Serial.println("[WS] PONG");
      }
      else if (strcmp(t, "error") == 0) {
        Serial.printf("[WS] ERROR: %s\n", doc["message"] | "Unknown");
      }
      break;
    }

    case WStype_ERROR:
      Serial.println("[WS] ERROR");
      wsConnected = false;
      break;

    default: break;
  }
}

void sensorWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      Serial.println("[LIVE WS] DISCONNECTED");
      sensorWsConnected = false;
      break;

    case WStype_CONNECTED:
      Serial.println("[LIVE WS] CONNECTED");
      sensorWsConnected = true;
      Serial.print("[LIVE WS] Server: ");
      Serial.println((char*)payload);

      // Trigger an immediate live send right after (re)connect
      lastLiveSensorSend = millis() - CFG::LIVE_SENSOR_INTERVAL;
      break;

    case WStype_TEXT: {
      StaticJsonDocument<512> doc;
      if (deserializeJson(doc, payload, length)) {
        Serial.println("[LIVE WS] JSON ERROR");
        break;
      }
      const char* t = doc["type"] | "";
      if (strcmp(t, "pong") == 0) {
        Serial.println("[LIVE WS] PONG");
      } else if (strcmp(t, "error") == 0) {
        Serial.printf("[LIVE WS] SERVER ERROR: %s\n", doc["message"] | "Unknown");
      } else {
        Serial.print("[LIVE WS] SERVER: ");
        Serial.println((char*)payload);
      }
      break;
    }

    case WStype_ERROR:
      Serial.println("[LIVE WS] ERROR");
      sensorWsConnected = false;
      break;

    default: break;
  }
}

void connectWebSocket() {
  String path = "/ws/auto-control/" + String(CFG::USER_ID) + "/" +
                String(CFG::POND_ID) +
                "?client=esp32&user_id=" + String(CFG::USER_ID);
  Serial.printf("WS CONNECT: %s:%d%s\n", CFG::HOST, CFG::PORT, path.c_str());
  webSocket.beginSSL(CFG::HOST, CFG::PORT, path);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
}

void connectSensorWebSocket() {
  String path = "/ws/sensor-data/" + String(CFG::POND_ID) +
                "?client=esp32&user_id=" + String(CFG::USER_ID);
  Serial.printf("[LIVE WS] CONNECT: %s:%d%s\n", CFG::HOST, CFG::PORT, path.c_str());
  sensorWebSocket.beginSSL(CFG::HOST, CFG::PORT, path);
  sensorWebSocket.onEvent(sensorWebSocketEvent);
  sensorWebSocket.setReconnectInterval(5000);
  sensorWebSocket.enableHeartbeat(15000, 3000, 2);
}

// ======================================================
// WIFI
// ======================================================
void scanWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(true);
  delay(1000);
  int n = WiFi.scanNetworks();
  Serial.printf("%d networks found\n", n);
  for (int i = 0; i < n; i++)
    Serial.printf("  %s | RSSI: %d | CH: %d\n",
                  WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
  WiFi.scanDelete();
}

bool connectToWiFi() {
  Serial.printf("Connecting to: %s\n", CFG::WIFI_SSID);
  WiFi.begin(CFG::WIFI_SSID, CFG::WIFI_PASSWORD);
  for (int i = 0; i < 30; i++) {
    delay(500);
    Serial.print(".");
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("\nWIFI CONNECTED! IP: %s | RSSI: %d\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }
  Serial.printf("\nWIFI FAILED! Status: %d\n", WiFi.status());
  return false;
}

void maintainWiFiConnection() {
  bool connected = (WiFi.status() == WL_CONNECTED);

  if (connected) {
    if (!wasWiFiConnected) {
      wasWiFiConnected = true;
      Serial.printf("\n[WIFI] RECONNECTED! IP: %s | RSSI: %d\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
    }
    return;
  }

  if (wasWiFiConnected) {
    wasWiFiConnected = false;
    Serial.println("\n[WIFI] DISCONNECTED!\n[WIFI] Waiting for reconnection...");
  }

  if (millis() - lastWiFiReconnectAttempt >= CFG::WIFI_RECONNECT_INTERVAL) {
    lastWiFiReconnectAttempt = millis();
    Serial.println("[WIFI] Attempting reconnection...");
    WiFi.disconnect();
    WiFi.begin(CFG::WIFI_SSID, CFG::WIFI_PASSWORD);
  }
}

// ======================================================
// SETUP
// ======================================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nESP32 AUTO CONTROL");

  // Relays
  pinMode(CFG::PIN_PUMP_1,  OUTPUT);
  pinMode(CFG::PIN_PUMP_2,  OUTPUT);
  pinMode(CFG::PIN_AERATOR, OUTPUT);
  pinMode(CFG::PIN_HEATER,  OUTPUT);
  digitalWrite(CFG::PIN_PUMP_1,  RELAY_OFF);
  digitalWrite(CFG::PIN_PUMP_2,  RELAY_OFF);
  digitalWrite(CFG::PIN_AERATOR, RELAY_OFF);
  digitalWrite(CFG::PIN_HEATER,  RELAY_OFF);
  Serial.println("Relays OFF: Pump(22/23) Aerator(19) Heater(21)");

  // ADC
  analogReadResolution(12);
  analogSetPinAttenuation(CFG::PIN_PH,        ADC_11db);
  analogSetPinAttenuation(CFG::PIN_TURBIDITY, ADC_11db);
  analogSetPinAttenuation(CFG::PIN_MQ,        ADC_11db);
  pinMode(CFG::PIN_PH,        INPUT_PULLDOWN);
  pinMode(CFG::PIN_TURBIDITY, INPUT_PULLDOWN);
  pinMode(CFG::PIN_MQ,        INPUT_PULLDOWN);

  ds18b20.begin();
  Serial.println("[SENSORS] Initialized");

  scanWiFi();
  if (connectToWiFi()) wasWiFiConnected = true;
  else {
    Serial.println("[WIFI] Initial connection failed.");
    Serial.println("[WIFI] Will continue trying in the background.");
  }

  connectWebSocket();
  connectSensorWebSocket();

  Serial.println("[BOOT] System ready");
}

// ======================================================
// LOOP
// ======================================================
void loop() {
  maintainWiFiConnection();
  webSocket.loop();
  sensorWebSocket.loop();
  processPendingAutomationPost();

  // ---- Sensors + safety every 2 s ----
  static unsigned long lastSensorRead = 0;
  if (millis() - lastSensorRead >= 2000) {
    lastSensorRead = millis();

    readSensors();
    checkSensorErrors();
    checkWaterQualityWarnings();

    if (tempValid && phValid && turbidityValid && ammoniaValid) {
      runAutomationRules();
    } else if (automationOn) {
      stopAutomationDevices();
      automationOn = false;
      Serial.println("[SAFETY] Sensor failure detected.\n"
                     "[SAFETY] Automation stopped.\n"
                     "[SAFETY] Switched to Manual mode.\n"
                     "[SAFETY] Manual control remains available.");
    } else {
      Serial.println("[SAFETY] Sensor failure detected.\n"
                     "[SAFETY] Manual control remains available.");
    }
  }
 
  // ---- Live WebSocket every 5 s ----
  if (sensorWsConnected &&
      millis() - lastLiveSensorSend >= CFG::LIVE_SENSOR_INTERVAL) {
    lastLiveSensorSend = millis();
    sendLiveSensorData();
  }

  // ---- DB recording every 20 min (first immediately) ----
  if (firstSensorRecord ||
      millis() - lastSensorRecord >= CFG::SENSOR_RECORD_INTERVAL) {
    firstSensorRecord = false;
    lastSensorRecord  = millis();
    Serial.println("\n================================\n[RECORD] Saving sensor data\n================================");
    postSensorData();
  }

  delay(5);
}
