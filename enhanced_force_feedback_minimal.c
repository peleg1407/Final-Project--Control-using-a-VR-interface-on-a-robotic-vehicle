/**
 * Minimal Force Feedback Program for Robot Control
 * Applies specific force feedback pattern based on input parameter
 *
 * Compile with MinGW:
 * gcc -o enhanced_force_feedback_minimal.exe enhanced_force_feedback_minimal.c -I"C:\Program Files (x86)\Microsoft DirectX SDK (June 2010)\Include" -L"C:\Program Files (x86)\Microsoft DirectX SDK (June 2010)\Lib\x64" -ldinput8 -ldxguid -lole32 -DDIRECTINPUT_VERSION=0x0800
 */

#define DIRECTINPUT_VERSION 0x0800
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <stdio.h>
#include <stdbool.h>
#include <dinput.h>

// Feedback types (match the Python code)
#define FEEDBACK_NONE 0
#define FEEDBACK_OBSTACLE 1
#define FEEDBACK_MOVEMENT 2

// Configuration
#define FORCE_STRENGTH 1          // Base force feedback strength (0-1 range)
#define OSCILLATION_DURATION 300    // Duration of feedback in ms


// Global variables
LPDIRECTINPUT8 g_pDI = NULL;
LPDIRECTINPUTDEVICE8 g_pJoystick = NULL;
LPDIRECTINPUTEFFECT g_pEffect = NULL;
bool g_bRunning = true;

// Function prototypes
BOOL CALLBACK EnumDevicesCallback(const DIDEVICEINSTANCE* pdidInstance, VOID* pContext);
bool InitializeDirectInput(void);
void CleanupAll(void);
void ApplyGentleOscillation(int feedbackType);

int main(int argc, char* argv[]) {
    HRESULT hr;
    int feedbackType = FEEDBACK_NONE;  // Default to obstacle

    // Parse command line arguments
    if (argc > 1) {
        feedbackType = atoi(argv[1]);
        if (feedbackType < FEEDBACK_NONE || feedbackType > FEEDBACK_MOVEMENT) {
            feedbackType = FEEDBACK_NONE;  // Default if invalid
        }
    }

    printf("Minimal Force Feedback - Type: %d\n", feedbackType);

    // Initialize COM
    hr = CoInitialize(NULL);
    if (FAILED(hr)) {
        printf("Failed to initialize COM. Error: 0x%lx\n", hr);
        return 1;
    }

    // Initialize DirectInput
    if (!InitializeDirectInput()) {
        CleanupAll();
        CoUninitialize();
        return 1;
    }

    // Apply the feedback
    if (feedbackType != FEEDBACK_NONE) {
        ApplyGentleOscillation(feedbackType);
    } else {
        printf("No feedback requested\n");
    }

    // Cleanup
    CleanupAll();
    CoUninitialize();
    return 0;
}

bool InitializeDirectInput(void) {
    HRESULT hr;

    // Create DirectInput object
    hr = DirectInput8Create(GetModuleHandle(NULL), DIRECTINPUT_VERSION,
                           &IID_IDirectInput8, (VOID**)&g_pDI, NULL);
    if (FAILED(hr)) {
        printf("Failed to create DirectInput object. Error: 0x%lx\n", hr);
        return false;
    }

    // Enumerate devices to find joystick
    printf("Looking for Force Feedback joystick...\n");
    hr = IDirectInput8_EnumDevices(g_pDI, DI8DEVCLASS_GAMECTRL, EnumDevicesCallback,
                           NULL, DIEDFL_ATTACHEDONLY | DIEDFL_FORCEFEEDBACK);
    if (FAILED(hr) || g_pJoystick == NULL) {
        printf("Failed to find Force Feedback joystick. Error: 0x%lx\n", hr);
        return false;
    }

    // Set cooperative level - use GetConsoleWindow() for console apps
    hr = IDirectInputDevice8_SetCooperativeLevel(g_pJoystick, GetConsoleWindow(),
                                         DISCL_EXCLUSIVE | DISCL_BACKGROUND);
    if (FAILED(hr)) {
        printf("Failed to set cooperative level. Error: 0x%lx\n", hr);
        return false;
    }

    // Set data format
    hr = IDirectInputDevice8_SetDataFormat(g_pJoystick, &c_dfDIJoystick);
    if (FAILED(hr)) {
        printf("Failed to set data format. Error: 0x%lx\n", hr);
        return false;
    }

    // Acquire the device
    hr = IDirectInputDevice8_Acquire(g_pJoystick);
    if (FAILED(hr)) {
        printf("Failed to acquire joystick. Error: 0x%lx\n", hr);
        return false;
    }

    printf("DirectInput initialized successfully\n");
    return true;
}

void ApplyGentleOscillation(int feedbackType) {
    HRESULT hr;
    DWORD startTime = GetTickCount();
    DWORD endTime = startTime + OSCILLATION_DURATION;
    DWORD currentTime;
    int direction = 1;
    int switchInterval;
    float strengthMultiplier;

    // Configure feedback pattern based on type
    switch (feedbackType) {
        case FEEDBACK_OBSTACLE:
            // Slower oscillation for obstacles - easier to recognize
            switchInterval = 25;  // 150ms per direction (slower)
            strengthMultiplier = 1;  // Stronger for obstacles
            printf("obstacle feedback pattern...\n");
            break;

        case FEEDBACK_MOVEMENT:
            // Faster oscillation for movement
            switchInterval = 25;   // 80ms per direction (faster)
            strengthMultiplier = 1;  // Moderate strength
            printf("Applying fast movement feedback pattern...\n");
            break;

            default:
            // Default gentle pattern
            switchInterval = 0;  // 100ms per direction
            strengthMultiplier = 0;  // Gentle
            printf("Applying default feedback pattern...\n");
    }

    DWORD lastSwitchTime = startTime;

    // Loop until the oscillation duration is complete
    while ((currentTime = GetTickCount()) < endTime) {
        // Check if it's time to switch direction
        if (currentTime - lastSwitchTime > switchInterval) {
            direction *= -1;  // Reverse direction
            lastSwitchTime = currentTime;

            // Create a gentle constant force effect
            DICONSTANTFORCE cf = { (LONG)(DI_FFNOMINALMAX * FORCE_STRENGTH * strengthMultiplier) };
            DIEFFECT eff;
            DWORD rgdwAxes[2] = { DIJOFS_X, DIJOFS_Y };
            LONG rglDirection[2] = { direction, 0 };  // Only apply force in X direction (side-to-side)

            // Initialize effect structure
            ZeroMemory(&eff, sizeof(eff));
            eff.dwSize = sizeof(DIEFFECT);
            eff.dwFlags = DIEFF_CARTESIAN | DIEFF_OBJECTOFFSETS;
            eff.dwDuration = switchInterval * 1000;  // Duration in microseconds
            eff.dwSamplePeriod = 0;                  // Default sample period
            eff.dwGain = DI_FFNOMINALMAX;            // No scaling
            eff.dwTriggerButton = DIEB_NOTRIGGER;    // No trigger button
            eff.dwTriggerRepeatInterval = 0;         // No repeat
            eff.cAxes = 2;                           // X and Y axes
            eff.rgdwAxes = rgdwAxes;
            eff.rglDirection = rglDirection;
            eff.lpEnvelope = NULL;                   // No envelope
            eff.cbTypeSpecificParams = sizeof(DICONSTANTFORCE);
            eff.lpvTypeSpecificParams = &cf;
            eff.dwStartDelay = 0;                    // No delay

            // Release existing effect if any
            if (g_pEffect != NULL) {
                IDirectInputEffect_Release(g_pEffect);
                g_pEffect = NULL;
            }

            // Create the effect
            hr = IDirectInputDevice8_CreateEffect(g_pJoystick, &GUID_ConstantForce, &eff, &g_pEffect, NULL);
            if (FAILED(hr)) {
                printf("Failed to create oscillation effect. Error: 0x%lx\n", hr);
                return;
            }

            // Start the effect
            hr = IDirectInputEffect_Start(g_pEffect, 1, 0);
            if (FAILED(hr)) {
                printf("Failed to start oscillation effect. Error: 0x%lx\n", hr);
            }
        }

        // Small delay to prevent CPU hogging
        Sleep(10);
    }

    // Stop the effect
    if (g_pEffect != NULL) {
        IDirectInputEffect_Stop(g_pEffect);
    }
}

// Callback function for enumerating devices
BOOL CALLBACK EnumDevicesCallback(const DIDEVICEINSTANCE* pdidInstance, VOID* pContext) {
    HRESULT hr;

    // Print device info
    printf("Found: %s\n", pdidInstance->tszProductName);

    // Create the DirectInput device
    hr = IDirectInput8_CreateDevice(g_pDI, &pdidInstance->guidInstance, &g_pJoystick, NULL);
    if (SUCCEEDED(hr)) {
        printf("Successfully created device interface\n");
        return DIENUM_STOP;  // Stop enumeration, we found a device
    } else {
        printf("Failed to create device interface. Error: 0x%lx\n", hr);
        return DIENUM_CONTINUE;  // Continue enumeration
    }
}

void CleanupAll(void) {
    // Release DirectInput objects
    if (g_pEffect != NULL) {
        IDirectInputEffect_Release(g_pEffect);
        g_pEffect = NULL;
    }

    if (g_pJoystick != NULL) {
        IDirectInputDevice8_Unacquire(g_pJoystick);
        IDirectInputDevice8_Release(g_pJoystick);
        g_pJoystick = NULL;
    }

    if (g_pDI != NULL) {
        IDirectInput8_Release(g_pDI);
        g_pDI = NULL;
    }

    printf("Cleanup complete\n");
}