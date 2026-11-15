// ArchipelagoHOI4.dll
//
// Executes HOI4 console commands received on \\.\pipe\hoi4_ap.
// Commands run on the game window thread. Logs to
// Documents\Paradox Interactive\Hearts of Iron IV\logs\archipelago_dll.log

#include "pch.h"

#include <windows.h>
#include <shlobj.h>
#include <knownfolders.h>

#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#ifdef _MSC_VER
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "user32.lib")
#endif

// --- Configuration ---
namespace cfg {
    // hoi4.exe 1.19.3 (build c01a3d507). The RVA is only used on this exact build.
    constexpr DWORD     KNOWN_PE_TIMESTAMP = 1788945339;
    constexpr uintptr_t KNOWN_RVA          = 0x24B4D60;

    // Console command function signature ("?" = wildcard), from tools/make_signature.py.
    // Stack frame size and local offset are wildcarded.
    constexpr const char* SIGNATURE = "48 89 5C 24 18 48 89 54 24 10 55 56 57 41 54 41 55 41 56 41 57 48 8B EC 48 83 EC ? 49 8B F0 4C 8B FA 4C 8B E9 BF 01 00 00 00 89 7D ? C6 02 00";

    constexpr const wchar_t* PIPE_NAME = L"\\\\.\\pipe\\hoi4_ap";
    constexpr DWORD COMMAND_TIMEOUT_MS = 5000;
    constexpr UINT  WM_AP_EXECUTE      = WM_APP + 0x4150;
}

// The game uses MSVC Release std::string (32 bytes): Debug builds are incompatible.
static_assert(sizeof(void*) == 8, "Build for x64");
#if defined(_MSC_VER) && defined(_DEBUG)
#error "Build in Release: Debug std::string does not match the game's layout"
#endif

template <typename T>
struct PdxArray {
    T*      data;      // 0x00
    int32_t capacity;  // 0x08
    int32_t size;      // 0x0C
};

using tExecuteCommand = void* (*)(void* pConsole, void* pResultOut, const void* pArgsArray);

// --- Global state ---
static std::atomic<uintptr_t> g_target{0};
static std::atomic<void*>     g_console{nullptr};
static std::atomic<HWND>      g_window{nullptr};
static std::atomic<bool>      g_hookInstalled{false};
static std::atomic<bool>      g_disabled{false};
static std::atomic<const char*> g_notReadyReason{"initializing"};

static BYTE    g_origBytes[14];
static WNDPROC g_origWndProc = nullptr;

struct PendingCommand {
    std::string      text;
    HANDLE           done = nullptr;
    std::atomic<int> state{0};   // 0 = pending, 1 = taken by the game, 2 = cancelled
    std::string      reply;
};
static std::mutex                                  g_queueMutex;
static std::deque<std::shared_ptr<PendingCommand>> g_queue;

// --- Log ---
static std::mutex   g_logMutex;
static std::wstring g_logPath;

static void InitLog() {
    PWSTR docs = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs))) {
        g_logPath = std::wstring(docs) + L"\\Paradox Interactive\\Hearts of Iron IV\\logs\\archipelago_dll.log";
        CoTaskMemFree(docs);
    }
}

static void Log(const char* fmt, ...) {
    char msg[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(msg, sizeof(msg), fmt, args);
    va_end(args);

    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[1100];
    snprintf(line, sizeof(line), "[%02d:%02d:%02d] %s\n", t.wHour, t.wMinute, t.wSecond, msg);
    OutputDebugStringA(line);

    std::lock_guard<std::mutex> lock(g_logMutex);
    if (g_logPath.empty()) return;
    FILE* f = nullptr;
    if (_wfopen_s(&f, g_logPath.c_str(), L"a") == 0 && f) {
        fputs(line, f);
        fclose(f);
    }
}

// --- Target lookup ---
static IMAGE_NT_HEADERS64* NtHeaders(uintptr_t base) {
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return nullptr;
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    return nt->Signature == IMAGE_NT_SIGNATURE ? nt : nullptr;
}

static bool TextSection(uintptr_t base, uintptr_t& start, size_t& size) {
    auto nt = NtHeaders(base);
    if (!nt) return false;
    auto sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec) {
        if (sec->Characteristics & IMAGE_SCN_MEM_EXECUTE) {
            start = base + sec->VirtualAddress;
            size  = sec->Misc.VirtualSize;
            return true;
        }
    }
    return false;
}

static bool ParseSignature(const char* sig, std::vector<int>& out) {
    std::istringstream ss(sig);
    std::string tok;
    while (ss >> tok) {
        if (tok == "?" || tok == "??") {
            out.push_back(-1);
        } else {
            char* end = nullptr;
            long v = strtol(tok.c_str(), &end, 16);
            if (*end != '\0' || v < 0 || v > 0xFF) return false;
            out.push_back(static_cast<int>(v));
        }
    }
    return !out.empty();
}

// Returns the address if the pattern matches exactly once, 0 otherwise.
static uintptr_t ScanUnique(uintptr_t start, size_t size, const std::vector<int>& pat, int& matches) {
    matches = 0;
    uintptr_t found = 0;
    const BYTE* p = reinterpret_cast<const BYTE*>(start);
    const size_t n = pat.size();
    for (size_t i = 0; i + n <= size; ++i) {
        size_t j = 0;
        while (j < n && (pat[j] < 0 || p[i + j] == pat[j])) ++j;
        if (j == n) {
            ++matches;
            found = start + i;
            if (matches > 1) return 0;
        }
    }
    return matches == 1 ? found : 0;
}

static uintptr_t FindTarget() {
    uintptr_t base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    auto nt = NtHeaders(base);
    if (!nt) {
        Log("ERREUR : en-tête PE de hoi4.exe illisible");
        return 0;
    }
    DWORD stamp = nt->FileHeader.TimeDateStamp;
    Log("hoi4.exe base=0x%llx TimeDateStamp=%lu", (unsigned long long)base, (unsigned long)stamp);

    uintptr_t textStart = 0;
    size_t textSize = 0;
    if (!TextSection(base, textStart, textSize)) {
        Log("ERREUR : section de code introuvable");
        return 0;
    }

    if (cfg::SIGNATURE[0] != '\0') {
        std::vector<int> pat;
        if (!ParseSignature(cfg::SIGNATURE, pat)) {
            Log("ERREUR : signature mal formée");
        } else {
            int matches = 0;
            uintptr_t addr = ScanUnique(textStart, textSize, pat, matches);
            if (addr && stamp == cfg::KNOWN_PE_TIMESTAMP && addr != base + cfg::KNOWN_RVA) {
                // on the reference build, signature and RVA must agree
                Log("ATTENTION : la signature pointe sur RVA 0x%llx au lieu de 0x%llx, elle est ignorée",
                    (unsigned long long)(addr - base), (unsigned long long)cfg::KNOWN_RVA);
            } else if (addr) {
                Log("Fonction console trouvée par signature (RVA 0x%llx)", (unsigned long long)(addr - base));
                return addr;
            }
            Log("Signature : %d correspondance(s), il en faut exactement 1", matches);
        }
    }

    if (stamp == cfg::KNOWN_PE_TIMESTAMP) {
        uintptr_t addr = base + cfg::KNOWN_RVA;
        if (addr >= textStart && addr + 14 <= textStart + textSize) {
            Log("Build connue : offset 0x%llx utilisé", (unsigned long long)cfg::KNOWN_RVA);
            return addr;
        }
    }

    Log("Version de HOI4 non supportée (TimeDateStamp %lu). Aucun hook installé : le jeu "
        "tourne normalement, mais les items AP ne seront pas appliqués. Il faut mettre à jour la "
        "signature (tools/make_signature.py).", (unsigned long)stamp);
    return 0;
}

// --- One-shot hook: captures the console pointer on the first command ---
static void* HookedExecuteCommand(void* pConsole, void* pResultOut, const void* pArgsArray) {
    uintptr_t target = g_target.load();

    DWORD oldProtect;
    VirtualProtect(reinterpret_cast<void*>(target), 14, PAGE_EXECUTE_READWRITE, &oldProtect);
    memcpy(reinterpret_cast<void*>(target), g_origBytes, 14);
    VirtualProtect(reinterpret_cast<void*>(target), 14, oldProtect, &oldProtect);
    FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(target), 14);
    g_hookInstalled = false;

    if (!g_console.load()) {
        g_console = pConsole;
        Log("Console capturée (0x%p)", pConsole);
        MessageBeep(MB_OK);
    }
    return reinterpret_cast<tExecuteCommand>(target)(pConsole, pResultOut, pArgsArray);
}

static bool InstallOneShotHook(uintptr_t target) {
    MEMORY_BASIC_INFORMATION mbi{};
    if (!VirtualQuery(reinterpret_cast<void*>(target), &mbi, sizeof(mbi)) ||
        !(mbi.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE))) {
        Log("ERREUR : l'adresse cible n'est pas du code exécutable");
        return false;
    }

    g_target = target;
    memcpy(g_origBytes, reinterpret_cast<void*>(target), 14);

    BYTE jmp[14] = {0xFF, 0x25, 0x00, 0x00, 0x00, 0x00};  // jmp qword ptr [rip+0]
    uintptr_t dest = reinterpret_cast<uintptr_t>(&HookedExecuteCommand);
    memcpy(jmp + 6, &dest, sizeof(dest));

    DWORD oldProtect;
    if (!VirtualProtect(reinterpret_cast<void*>(target), 14, PAGE_EXECUTE_READWRITE, &oldProtect)) {
        Log("ERREUR : VirtualProtect a échoué (%lu)", GetLastError());
        return false;
    }
    memcpy(reinterpret_cast<void*>(target), jmp, 14);
    VirtualProtect(reinterpret_cast<void*>(target), 14, oldProtect, &oldProtect);
    FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(target), 14);
    g_hookInstalled = true;
    return true;
}

// --- Game-thread execution ---

// No C++ objects with destructors here (required by __try/__except).
static DWORD SafeCall(tExecuteCommand fn, void* console, void* result, const void* args) {
#ifdef _MSC_VER
    __try {
        fn(console, result, args);
        return 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return GetExceptionCode();
    }
#else
    fn(console, result, args);
    return 0;
#endif
}

static std::string ExecuteOnGameThread(const std::string& command) {
    if (g_disabled) return "DISABLED";
    void* console = g_console.load();
    if (!console) return "NOT_READY:console_not_captured";

    std::vector<std::string> tokens;
    std::istringstream ss(command);
    std::string word;
    while (ss >> word) tokens.push_back(word);
    if (tokens.empty()) return "ERROR:empty";

    alignas(16) char resultBuffer[256] = {0};
    PdxArray<std::string> argsArray;
    argsArray.data     = tokens.data();
    argsArray.capacity = static_cast<int32_t>(tokens.size());
    argsArray.size     = static_cast<int32_t>(tokens.size());

    DWORD code = SafeCall(reinterpret_cast<tExecuteCommand>(g_target.load()), console, resultBuffer, &argsArray);
    if (code != 0) {
        g_disabled = true;
        Log("EXCEPTION 0x%08lx pendant '%s' : exécution des commandes désactivée. "
            "Sauvegarde et redémarre le jeu.", (unsigned long)code, command.c_str());
        return "ERROR:exception";
    }
    Log("OK : %s", command.c_str());
    return "OK";
}

static void DrainQueue() {
    std::deque<std::shared_ptr<PendingCommand>> local;
    {
        std::lock_guard<std::mutex> lock(g_queueMutex);
        local.swap(g_queue);
    }
    for (auto& cmd : local) {
        int expected = 0;
        if (!cmd->state.compare_exchange_strong(expected, 1)) continue;  // cancelled
        cmd->reply = ExecuteOnGameThread(cmd->text);
        SetEvent(cmd->done);
    }
}

static LRESULT CALLBACK ApWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == cfg::WM_AP_EXECUTE) {
        DrainQueue();
        return 0;
    }
    return CallWindowProcW(g_origWndProc, hwnd, msg, wParam, lParam);
}

static BOOL CALLBACK EnumProc(HWND hwnd, LPARAM lParam) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(hwnd) && !GetWindow(hwnd, GW_OWNER)) {
        *reinterpret_cast<HWND*>(lParam) = hwnd;
        return FALSE;
    }
    return TRUE;
}

static void AttachToGameWindow() {
    for (;;) {
        HWND hwnd = nullptr;
        EnumWindows(EnumProc, reinterpret_cast<LPARAM>(&hwnd));
        if (hwnd) {
            g_origWndProc = reinterpret_cast<WNDPROC>(
                SetWindowLongPtrW(hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&ApWndProc)));
            if (g_origWndProc) {
                g_window = hwnd;
                Log("Fenêtre du jeu trouvée (0x%p)", (void*)hwnd);
                return;
            }
            Log("ERREUR : SetWindowLongPtr a échoué (%lu)", GetLastError());
        }
        Sleep(500);
    }
}

// --- Pipe ---
static const char* NotReadyReason() {
    if (g_disabled) return "disabled_after_exception";
    if (!g_target.load()) return g_notReadyReason.load();
    if (!g_window.load()) return "no_window";
    if (!g_console.load()) return "console_not_captured";
    return nullptr;
}

static std::string HandleRequest(const std::string& request) {
    if (request == "PING") {
        const char* reason = NotReadyReason();
        return reason ? std::string("NOT_READY:") + reason : "READY";
    }
    if (request.rfind("CMD ", 0) != 0) return "ERROR:unknown_request";

    const char* reason = NotReadyReason();
    if (reason) return std::string("NOT_READY:") + reason;

    auto cmd = std::make_shared<PendingCommand>();
    cmd->text = request.substr(4);
    cmd->done = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    {
        std::lock_guard<std::mutex> lock(g_queueMutex);
        g_queue.push_back(cmd);
    }
    PostMessageW(g_window.load(), cfg::WM_AP_EXECUTE, 0, 0);

    std::string reply;
    if (WaitForSingleObject(cmd->done, cfg::COMMAND_TIMEOUT_MS) == WAIT_OBJECT_0) {
        reply = cmd->reply;
    } else {
        int expected = 0;
        if (cmd->state.compare_exchange_strong(expected, 2)) {
            reply = "TIMEOUT";  // never executed, safe to retry
        } else {
            WaitForSingleObject(cmd->done, INFINITE);  // taken by the game meanwhile
            reply = cmd->reply;
        }
    }
    CloseHandle(cmd->done);
    return reply;
}

static DWORD WINAPI PipeServerThread(LPVOID) {
    HANDLE pipe = CreateNamedPipeW(cfg::PIPE_NAME, PIPE_ACCESS_DUPLEX,
                                   PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                                   1, 4096, 4096, 0, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        Log("ERREUR : CreateNamedPipe a échoué (%lu)", GetLastError());
        return 1;
    }
    Log("Pipe prêt");

    char buffer[2048];
    for (;;) {
        BOOL connected = ConnectNamedPipe(pipe, nullptr) || GetLastError() == ERROR_PIPE_CONNECTED;
        if (!connected) {
            Sleep(100);
            continue;
        }
        Log("Client connecté");
        for (;;) {
            DWORD read = 0;
            BOOL ok = ReadFile(pipe, buffer, sizeof(buffer) - 1, &read, nullptr);
            std::string reply;
            if (!ok && GetLastError() == ERROR_MORE_DATA) {
                // discard oversized message
                while (!ReadFile(pipe, buffer, sizeof(buffer), &read, nullptr) &&
                       GetLastError() == ERROR_MORE_DATA) {}
                reply = "ERROR:too_long";
            } else if (!ok) {
                break;  // client disconnected
            } else {
                buffer[read] = '\0';
                std::string request(buffer, read);
                while (!request.empty() && (request.back() == '\n' || request.back() == '\r'))
                    request.pop_back();
                reply = HandleRequest(request);
            }
            DWORD written = 0;
            if (!WriteFile(pipe, reply.data(), static_cast<DWORD>(reply.size()), &written, nullptr)) break;
        }
        DisconnectNamedPipe(pipe);
        Log("Client déconnecté");
    }
}

// --- Initialization (outside DllMain) ---
static DWORD WINAPI InitThread(LPVOID) {
    InitLog();
    Log("=== ArchipelagoHOI4.dll chargée ===");

    uintptr_t target = FindTarget();
    if (target) {
        if (InstallOneShotHook(target)) {
            Log("Hook installé : tape n'importe quelle commande dans la console pour l'armer");
        } else {
            g_target = 0;
            g_notReadyReason = "hook_failed";
        }
    } else {
        g_notReadyReason = "unsupported_version";
    }

    HANDLE h = CreateThread(nullptr, 0, PipeServerThread, nullptr, 0, nullptr);
    if (h) CloseHandle(h);

    AttachToGameWindow();
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        HANDLE h = CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
        if (h) CloseHandle(h);
    }
    return TRUE;
}
