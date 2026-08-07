# sdk/c/CMakeLists.txt
cmake_minimum_required(VERSION 3.16)
project(iot-platform-c-sdk VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# -- Dependencies (cJSON via FetchContent; paho/curl/openssl via system) --
include(FetchContent)
FetchContent_Declare(
  cjson
  GIT_REPOSITORY https://github.com/DaveGamble/cJSON.git
  GIT_TAG        v1.7.17
)
set(CJSON_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(ENABLE_CJSON_TEST       OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(cjson)

find_package(OpenSSL REQUIRED)
find_package(CURL    REQUIRED)

find_library(PAHO_MQTT3 paho-mqtt3as REQUIRED)
find_path(PAHO_INCLUDE_DIR MQTTClient.h
          PATHS /usr/include /usr/local/include)
if(NOT PAHO_INCLUDE_DIR)
  message(FATAL_ERROR "MQTTClient.h not found — install libpaho-mqtt-dev")
endif()

# -- SDK library --
add_library(iot_platform STATIC
  src/iot_client.c
  src/provisioning.c
  src/ota.c
)
target_include_directories(iot_platform
  PUBLIC  include
  PRIVATE ${PAHO_INCLUDE_DIR}
)
target_link_libraries(iot_platform
  PRIVATE
    ${PAHO_MQTT3}
    CURL::libcurl
    OpenSSL::SSL
    OpenSSL::Crypto
    cjson
)

# -- Tests --
add_executable(test_compile tests/test_compile.c)
target_link_libraries(test_compile iot_platform)

enable_testing()
add_test(NAME compile_link_check COMMAND test_compile)

# -- Install headers --
install(FILES include/iot_client.h
        DESTINATION include/iot_platform
        OPTIONAL)
install(TARGETS iot_platform DESTINATION lib)
