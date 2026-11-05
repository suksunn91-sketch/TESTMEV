#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show) {
    wchar_t root[32768], python[32768], script[32768], command[65536], data[32768], log[32768];
    DWORD size = GetModuleFileNameW(NULL, root, 32768);
    if (!size || size >= 32768) return 1;
    wchar_t *slash = wcsrchr(root, L'\\');
    if (!slash) return 1;
    *slash = 0;
    swprintf(python, 32768, L"%ls\\runtime\\python.exe", root);
    swprintf(script, 32768, L"%ls\\app\\main.py", root);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,L"Please extract the entire ZIP first. Keep TESTMEV.exe beside the app and runtime folders.",L"TESTMEV - missing files",MB_OK|MB_ICONERROR);
        return 2;
    }
    HANDLE mutex=CreateMutexW(NULL,TRUE,L"Local\\TESTMEVDesktop-v1");
    if (GetLastError()==ERROR_ALREADY_EXISTS) {
        MessageBoxW(NULL,L"TESTMEV is already running. Use its existing window, or choose Exit in the app before opening it again.",L"TESTMEV",MB_OK|MB_ICONINFORMATION);
        if(mutex)CloseHandle(mutex);
        return 0;
    }
    swprintf(command,65536,L"\"%ls\" -I -u \"%ls\" %ls",python,script,arguments);
    STARTUPINFOW startup={0};startup.cb=sizeof(startup);
    PROCESS_INFORMATION process={0};
    HANDLE output=INVALID_HANDLE_VALUE;
    DWORD length=GetEnvironmentVariableW(L"LOCALAPPDATA",data,32768);
    if(length && length<32000){
        wcscat(data,L"\\TESTMEV");CreateDirectoryW(data,NULL);
        swprintf(log,32768,L"%ls\\launcher.log",data);
        SECURITY_ATTRIBUTES sa={sizeof(sa),NULL,TRUE};
        output=CreateFileW(log,GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
        if(output!=INVALID_HANDLE_VALUE){startup.dwFlags|=STARTF_USESTDHANDLES;startup.hStdOutput=output;startup.hStdError=output;startup.hStdInput=GetStdHandle(STD_INPUT_HANDLE);}
    }
    if(!CreateProcessW(python,command,NULL,NULL,TRUE,CREATE_NO_WINDOW,NULL,root,&startup,&process)){
        MessageBoxW(NULL,L"Unable to start the bundled Python runtime. Extract the full ZIP into a writable folder and try again.",L"TESTMEV",MB_OK|MB_ICONERROR);
        if(output!=INVALID_HANDLE_VALUE)CloseHandle(output);
        if(mutex)CloseHandle(mutex);
        return 3;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess,INFINITE);
    DWORD code=1;GetExitCodeProcess(process.hProcess,&code);CloseHandle(process.hProcess);
    if(output!=INVALID_HANDLE_VALUE)CloseHandle(output);
    if(mutex){ReleaseMutex(mutex);CloseHandle(mutex);}
    if(code)MessageBoxW(NULL,L"The app stopped unexpectedly. See %LOCALAPPDATA%\\TESTMEV\\launcher.log for details.",L"TESTMEV",MB_OK|MB_ICONERROR);
    return (int)code;
}
