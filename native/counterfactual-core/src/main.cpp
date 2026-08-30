#include "counterfactual/engine.hpp"
#include "counterfactual/json.hpp"
#include "counterfactual/model.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

int main(int argc, char** argv) {
    try {
        if (argc != 3) {
            std::cerr << "usage: counterfactual-core <request.json> <response.json>\n";
            return 2;
        }
        std::ifstream input(argv[1], std::ios::binary);
        if (!input) throw std::runtime_error("cannot open request");
        std::ostringstream buffer;
        buffer << input.rdbuf();
        const auto request = counterfactual::request_from_json(counterfactual::json::parse(buffer.str()));
        const auto response = counterfactual::calculate(request);
        std::ofstream output(argv[2], std::ios::binary | std::ios::trunc);
        if (!output) throw std::runtime_error("cannot open response");
        output << counterfactual::json::serialize(counterfactual::response_to_json(response), 2) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "counterfactual-core: " << error.what() << '\n';
        return 1;
    }
}
