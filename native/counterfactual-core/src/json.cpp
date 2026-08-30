#include "counterfactual/json.hpp"

#include <charconv>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace counterfactual::json {

bool Value::is_null() const { return std::holds_alternative<std::nullptr_t>(value_); }
bool Value::is_bool() const { return std::holds_alternative<bool>(value_); }
bool Value::is_number() const { return std::holds_alternative<double>(value_); }
bool Value::is_string() const { return std::holds_alternative<std::string>(value_); }
bool Value::is_array() const { return std::holds_alternative<Array>(value_); }
bool Value::is_object() const { return std::holds_alternative<Object>(value_); }
bool Value::as_bool() const { return std::get<bool>(value_); }
double Value::as_number() const { return std::get<double>(value_); }
const std::string& Value::as_string() const { return std::get<std::string>(value_); }
const Value::Array& Value::as_array() const { return std::get<Array>(value_); }
const Value::Object& Value::as_object() const { return std::get<Object>(value_); }
Value::Array& Value::as_array() { return std::get<Array>(value_); }
Value::Object& Value::as_object() { return std::get<Object>(value_); }

namespace {

class Parser {
public:
    explicit Parser(const std::string& text) : text_(text) {}

    Value parse_document() {
        auto value = parse_value();
        whitespace();
        if (position_ != text_.size()) {
            fail("trailing characters");
        }
        return value;
    }

private:
    const std::string& text_;
    std::size_t position_{};

    [[noreturn]] void fail(const std::string& message) const {
        throw std::runtime_error("JSON at byte " + std::to_string(position_) + ": " + message);
    }

    void whitespace() {
        while (position_ < text_.size() &&
               (text_[position_] == ' ' || text_[position_] == '\n' ||
                text_[position_] == '\r' || text_[position_] == '\t')) {
            ++position_;
        }
    }

    bool consume(char expected) {
        whitespace();
        if (position_ < text_.size() && text_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    void literal(const std::string& expected) {
        if (text_.substr(position_, expected.size()) != expected) {
            fail("expected " + expected);
        }
        position_ += expected.size();
    }

    Value parse_value() {
        whitespace();
        if (position_ >= text_.size()) fail("unexpected end");
        const char token = text_[position_];
        if (token == '{') return parse_object();
        if (token == '[') return parse_array();
        if (token == '"') return Value(parse_string());
        if (token == 't') { literal("true"); return Value(true); }
        if (token == 'f') { literal("false"); return Value(false); }
        if (token == 'n') { literal("null"); return Value(nullptr); }
        if (token == '-' || (token >= '0' && token <= '9')) return parse_number();
        fail("unexpected token");
    }

    Value parse_object() {
        consume('{');
        Value::Object object;
        if (consume('}')) return object;
        while (true) {
            whitespace();
            if (position_ >= text_.size() || text_[position_] != '"') fail("expected object key");
            auto key = parse_string();
            if (!consume(':')) fail("expected colon");
            if (!object.emplace(std::move(key), parse_value()).second) fail("duplicate object key");
            if (consume('}')) break;
            if (!consume(',')) fail("expected comma");
        }
        return object;
    }

    Value parse_array() {
        consume('[');
        Value::Array array;
        if (consume(']')) return array;
        while (true) {
            array.push_back(parse_value());
            if (consume(']')) break;
            if (!consume(',')) fail("expected comma");
        }
        return array;
    }

    static void append_utf8(std::string& result, unsigned codepoint) {
        if (codepoint <= 0x7F) result.push_back(static_cast<char>(codepoint));
        else if (codepoint <= 0x7FF) {
            result.push_back(static_cast<char>(0xC0 | (codepoint >> 6)));
            result.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
        } else {
            result.push_back(static_cast<char>(0xE0 | (codepoint >> 12)));
            result.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
            result.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
        }
    }

    std::string parse_string() {
        if (!consume('"')) fail("expected string");
        std::string result;
        while (position_ < text_.size()) {
            char value = text_[position_++];
            if (value == '"') return result;
            if (static_cast<unsigned char>(value) < 0x20) fail("control character in string");
            if (value != '\\') { result.push_back(value); continue; }
            if (position_ >= text_.size()) fail("unfinished escape");
            const char escaped = text_[position_++];
            switch (escaped) {
                case '"': result.push_back('"'); break;
                case '\\': result.push_back('\\'); break;
                case '/': result.push_back('/'); break;
                case 'b': result.push_back('\b'); break;
                case 'f': result.push_back('\f'); break;
                case 'n': result.push_back('\n'); break;
                case 'r': result.push_back('\r'); break;
                case 't': result.push_back('\t'); break;
                case 'u': {
                    if (position_ + 4 > text_.size()) fail("unfinished unicode escape");
                    unsigned codepoint{};
                    const auto* first = text_.data() + position_;
                    const auto parsed = std::from_chars(first, first + 4, codepoint, 16);
                    if (parsed.ec != std::errc{} || parsed.ptr != first + 4) fail("invalid unicode escape");
                    position_ += 4;
                    append_utf8(result, codepoint);
                    break;
                }
                default: fail("invalid escape");
            }
        }
        fail("unterminated string");
    }

    Value parse_number() {
        whitespace();
        const auto start = position_;
        if (text_[position_] == '-') ++position_;
        while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) ++position_;
        if (position_ < text_.size() && text_[position_] == '.') {
            ++position_;
            while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) ++position_;
        }
        if (position_ < text_.size() && (text_[position_] == 'e' || text_[position_] == 'E')) {
            ++position_;
            if (position_ < text_.size() && (text_[position_] == '+' || text_[position_] == '-')) ++position_;
            while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) ++position_;
        }
        try {
            const double value = std::stod(text_.substr(start, position_ - start));
            if (!std::isfinite(value)) fail("non-finite number");
            return value;
        } catch (const std::exception&) {
            fail("invalid number");
        }
    }
};

std::string escape(const std::string& value) {
    std::ostringstream output;
    for (const unsigned char byte : value) {
        switch (byte) {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if (byte < 0x20) output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(byte);
                else output << static_cast<char>(byte);
        }
    }
    return output.str();
}

void write(const Value& value, std::ostringstream& output, int indent, int depth) {
    const auto newline = [&] { if (indent > 0) output << '\n' << std::string(depth * indent, ' '); };
    if (value.is_null()) output << "null";
    else if (value.is_bool()) output << (value.as_bool() ? "true" : "false");
    else if (value.is_number()) output << std::setprecision(17) << value.as_number();
    else if (value.is_string()) output << '"' << escape(value.as_string()) << '"';
    else if (value.is_array()) {
        output << '[';
        bool first = true;
        for (const auto& item : value.as_array()) {
            if (!first) output << ',';
            if (indent > 0) output << '\n' << std::string((depth + 1) * indent, ' ');
            write(item, output, indent, depth + 1);
            first = false;
        }
        if (!first) newline();
        output << ']';
    } else {
        output << '{';
        bool first = true;
        for (const auto& [key, item] : value.as_object()) {
            if (!first) output << ',';
            if (indent > 0) output << '\n' << std::string((depth + 1) * indent, ' ');
            output << '"' << escape(key) << "\":" << (indent > 0 ? " " : "");
            write(item, output, indent, depth + 1);
            first = false;
        }
        if (!first) newline();
        output << '}';
    }
}

}  // namespace

Value parse(const std::string& text) { return Parser(text).parse_document(); }

std::string serialize(const Value& value, int indent) {
    std::ostringstream output;
    write(value, output, indent, 0);
    return output.str();
}

}  // namespace counterfactual::json
