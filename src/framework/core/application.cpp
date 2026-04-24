/*
 * Copyright (c) 2010-2017 OTClient <https://github.com/edubart/otclient>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include "application.h"
#include <csignal>
#include <framework/core/clock.h>
#include <framework/core/resourcemanager.h>
#include <framework/core/modulemanager.h>
#include <framework/core/eventdispatcher.h>
#include <framework/core/configmanager.h>
#include "asyncdispatcher.h"
#include <framework/luaengine/luainterface.h>
#include <framework/platform/crashhandler.h>
#include <framework/platform/platform.h>
#include <framework/http/http.h>

// Troyale 2026-04-24: substituido boost::process (removido em boost 1.88+)
// por spawn C puro. Uso: restart do proprio binario apos updater — basta spawnar filho.
#if not(defined(ANDROID) || defined(FREE_VERSION))
#ifdef _WIN32
#include <process.h>
#else
#include <cstdlib>
#endif
#endif

#include <locale>

#include <framework/net/connection.h>
#include <framework/proxy/proxy.h>

void exitSignalHandler(int sig)
{
    static bool signaled = false;
    switch(sig) {
        case SIGTERM:
        case SIGINT:
            if(!signaled && !g_app.isStopping() && !g_app.isTerminated()) {
                signaled = true;
                g_dispatcher.addEvent(std::bind(&Application::close, &g_app));
            }
            break;
    }
}

Application::Application()
{
    m_appName = "application";
    m_appCompactName = "app";
    m_appVersion = "none";
    m_charset = "cp1252";
    m_stopping = false;
#ifdef ANDROID
    m_mobile = true;
#endif
}

void Application::init(std::vector<std::string>& args)
{
    // capture exit signals
    signal(SIGTERM, exitSignalHandler);
    signal(SIGINT, exitSignalHandler);

    // setup locale
    std::locale::global(std::locale());

    // process args encoding
    g_platform.processArgs(args);

    g_asyncDispatcher.init();

    std::string startupOptions;
    for(uint i=1;i<args.size();++i) {
        const std::string& arg = args[i];
        startupOptions += " ";
        startupOptions += arg;
    }
    if(startupOptions.length() > 0)
        g_logger.info(stdext::format("Startup options: %s", startupOptions));

    m_startupOptions = startupOptions;

    // mobile testing
    if (startupOptions.find("-mobile") != std::string::npos)
        m_mobile = true;

    // initialize configs
    g_configs.init();

    // initialize lua
    g_lua.init();
    registerLuaFunctions();

    // initalize proxy
    g_proxy.init();
}

void Application::deinit()
{
    g_lua.callGlobalField("g_app", "onTerminate");

    // run modules unload events
    g_modules.unloadModules();
    g_modules.clear();

    // release remaining lua object references
    g_lua.collectGarbage();

    // poll remaining events
    poll();

    // disable dispatcher events
    g_dispatcher.shutdown();
}

void Application::terminate()
{
    // terminate network
    Connection::terminate();

    // release configs
    g_configs.terminate();

    // release resources
    g_resources.terminate();

    // terminate script environment
    g_lua.terminate();

    // terminate proxy
    g_proxy.terminate();

    m_terminated = true;

    signal(SIGTERM, SIG_DFL);
    signal(SIGINT, SIG_DFL);
}

void Application::poll()
{
    Connection::poll();

    g_dispatcher.poll();

    // poll connection again to flush pending write
    Connection::poll();
}

void Application::exit()
{
    g_lua.callGlobalField<bool>("g_app", "onExit");
    m_stopping = true;
}

void Application::quick_exit()
{
#ifdef _MSC_VER
    ::quick_exit(0);
#else
    ::exit(0);
#endif
}

void Application::close()
{
    if(!g_lua.callGlobalField<bool>("g_app", "onClose"))
        exit();
}

void Application::restart()
{
#if not(defined(ANDROID) || defined(FREE_VERSION))
    std::string bin = g_resources.getBinaryName();
#ifdef _WIN32
    // _P_NOWAIT: spawn nao bloqueia, processo filho fica independente (detach)
    _spawnl(_P_NOWAIT, bin.c_str(), bin.c_str(), (const char*)nullptr);
#else
    std::string cmd = bin + " &";
    (void)std::system(cmd.c_str());
#endif
    quick_exit();
#else
    exit();
#endif
}

void Application::restartArgs(const std::vector<std::string>& args)
{
#if not(defined(ANDROID) || defined(FREE_VERSION))
    std::string bin = g_resources.getBinaryName();
#ifdef _WIN32
    // Win: monta array argv pra _spawnv
    std::vector<const char*> argv;
    argv.push_back(bin.c_str());
    for (const auto& a : args) argv.push_back(a.c_str());
    argv.push_back(nullptr);
    _spawnv(_P_NOWAIT, bin.c_str(), (char* const*)argv.data());
#else
    std::string cmd = bin;
    for (const auto& a : args) { cmd += " "; cmd += a; }
    cmd += " &";
    (void)std::system(cmd.c_str());
#endif
    quick_exit();
#else
    exit();
#endif
}

std::string Application::getOs()
{
#if defined(ANDROID)
    return "android";
#elif defined(WIN32)
    return "windows";
#elif defined(__APPLE__)
    return "mac";
#elif __linux
    return "linux";
#else
    return "unknown";
#endif
}

