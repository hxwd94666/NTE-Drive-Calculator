#pragma once

#include "counterfactual/model.hpp"

namespace counterfactual {

[[nodiscard]] Response calculate(const Request& request);

}  // namespace counterfactual
