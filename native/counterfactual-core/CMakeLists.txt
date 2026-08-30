cmake_minimum_required(VERSION 3.20)
project(counterfactual_core VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(counterfactual_core_lib
    src/engine.cpp
    src/json.cpp
    src/model.cpp
)
target_include_directories(counterfactual_core_lib PUBLIC include)

function(counterfactual_strict_compile target)
    if(MSVC)
        target_compile_options(${target} PRIVATE /W4 /permissive- /utf-8)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    endif()
endfunction()
counterfactual_strict_compile(counterfactual_core_lib)

add_executable(counterfactual-core src/main.cpp)
target_link_libraries(counterfactual-core PRIVATE counterfactual_core_lib)
counterfactual_strict_compile(counterfactual-core)

enable_testing()
add_executable(counterfactual-core-tests tests/test_main.cpp)
target_link_libraries(counterfactual-core-tests PRIVATE counterfactual_core_lib)
counterfactual_strict_compile(counterfactual-core-tests)
target_compile_definitions(counterfactual-core-tests PRIVATE
    COUNTERFACTUAL_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures"
)
add_test(NAME counterfactual-core-tests COMMAND counterfactual-core-tests)
