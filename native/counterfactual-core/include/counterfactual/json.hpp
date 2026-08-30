#pragma once

#include <map>
#include <string>
#include <variant>
#include <vector>

namespace counterfactual::json {

class Value {
public:
    using Array = std::vector<Value>;
    using Object = std::map<std::string, Value, std::less<>>;
    using Storage = std::variant<std::nullptr_t, bool, double, std::string, Array, Object>;

    Value() : value_(nullptr) {}
    Value(std::nullptr_t) : value_(nullptr) {}
    Value(bool value) : value_(value) {}
    Value(double value) : value_(value) {}
    Value(int value) : value_(static_cast<double>(value)) {}
    Value(std::string value) : value_(std::move(value)) {}
    Value(const char* value) : value_(std::string(value)) {}
    Value(Array value) : value_(std::move(value)) {}
    Value(Object value) : value_(std::move(value)) {}

    [[nodiscard]] bool is_null() const;
    [[nodiscard]] bool is_bool() const;
    [[nodiscard]] bool is_number() const;
    [[nodiscard]] bool is_string() const;
    [[nodiscard]] bool is_array() const;
    [[nodiscard]] bool is_object() const;
    [[nodiscard]] bool as_bool() const;
    [[nodiscard]] double as_number() const;
    [[nodiscard]] const std::string& as_string() const;
    [[nodiscard]] const Array& as_array() const;
    [[nodiscard]] const Object& as_object() const;
    [[nodiscard]] Array& as_array();
    [[nodiscard]] Object& as_object();

private:
    Storage value_;
};

[[nodiscard]] Value parse(const std::string& text);
[[nodiscard]] std::string serialize(const Value& value, int indent = 2);

}  // namespace counterfactual::json
