#include <windows.h>
#include <iostream>
#include <string>
#include <vector>
#include <sstream>

template <typename T>
struct PdxArray {
    T* data;            // 0x00 
    int32_t capacity;   // 0x08 
    int32_t size;       // 0x0C 
};

typedef void* (*tExecuteCommand)(void* pConsole, void* pResultOut, const void* pArgsArray);

void* g_pConsole = nullptr;
uintptr_t g_targetAddr = 0;
BYTE g_origBytes[14];
bool g_isHooked = false;

void* hkExecuteCommand(void* pConsole, void* pResultOut, const void* pArgsArray) {
    if (!g_pConsole) {
        g_pConsole = pConsole;
        MessageBeep(MB_OK);
    }

    if (g_isHooked) {
        DWORD oldProtect;
        VirtualProtect((void*)g_targetAddr, 14, PAGE_EXECUTE_READWRITE, &oldProtect);
        memcpy((void*)g_targetAddr, g_origBytes, 14);
        VirtualProtect((void*)g_targetAddr, 14, oldProtect, &oldProtect);
        FlushInstructionCache(GetCurrentProcess(), (void*)g_targetAddr, 14);
        g_isHooked = false;
    }

    return ((tExecuteCommand)g_targetAddr)(pConsole, pResultOut, pArgsArray);
}

void InstallOneShotHook(uintptr_t targetAddr) {
    g_targetAddr = targetAddr;
    memcpy(g_origBytes, (void*)targetAddr, 14);

    DWORD oldProtect;
    VirtualProtect((void*)targetAddr, 14, PAGE_EXECUTE_READWRITE, &oldProtect);
    BYTE jmpCode[14] = { 0xFF, 0x25, 0x00, 0x00, 0x00, 0x00 };
    *(uintptr_t*)(jmpCode + 6) = (uintptr_t)hkExecuteCommand;
    memcpy((void*)targetAddr, jmpCode, 14);
    VirtualProtect((void*)targetAddr, 14, oldProtect, &oldProtect);
    FlushInstructionCache(GetCurrentProcess(), (void*)targetAddr, 14);

    g_isHooked = true;
}

DWORD WINAPI PipeServerThread(LPVOID lpParam) {
    HANDLE hPipe = CreateNamedPipeA(
        "\\\\.\\pipe\\hoi4_ap",
        PIPE_ACCESS_INBOUND,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
        1,
        2048,
        2048,
        0,
        NULL
    );

    if (hPipe == INVALID_HANDLE_VALUE) {
        return 1;
    }

    char buffer[1024];
    DWORD bytesRead;

    while (true) {
        if (ConnectNamedPipe(hPipe, NULL) != FALSE || GetLastError() == ERROR_PIPE_CONNECTED) {
            while (ReadFile(hPipe, buffer, sizeof(buffer) - 1, &bytesRead, NULL) != FALSE) {
                buffer[bytesRead] = '\0';

                while (bytesRead > 0 && (buffer[bytesRead - 1] == '\r' || buffer[bytesRead - 1] == '\n')) {
                    buffer[--bytesRead] = '\0';
                }

                if (g_pConsole && bytesRead > 0) {
                    std::vector<std::string> tokens;
                    std::stringstream ss(buffer);
                    std::string word;
                    while (ss >> word) {
                        tokens.push_back(word);
                    }

                    if (!tokens.empty()) {
                        alignas(16) char resultBuffer[256] = { 0 };
                        PdxArray<std::string> argsArray;
                        argsArray.data = tokens.data();
                        argsArray.capacity = static_cast<int32_t>(tokens.size());
                        argsArray.size = static_cast<int32_t>(tokens.size());

                        ((tExecuteCommand)g_targetAddr)(g_pConsole, resultBuffer, &argsArray);
                    }
                }
            }
            DisconnectNamedPipe(hPipe);
        }
    }

    CloseHandle(hPipe);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    if (ul_reason_for_call == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);

        uintptr_t baseAddress = reinterpret_cast<uintptr_t>(GetModuleHandleA(NULL));
        InstallOneShotHook(baseAddress + 0x24B4D60);

        CreateThread(NULL, 0, PipeServerThread, NULL, 0, NULL);
    }
    return TRUE;
}