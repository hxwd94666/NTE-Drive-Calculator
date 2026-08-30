#pragma once

#include "counterfactual/json.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace counterfactual {

struct Axis {
    bool complete{};
    std::int64_t range_start_us{};
    std::int64_t range_end_us{};
};

struct Character {
    int character_id{};
    double character_level{80.0};
    std::map<std::string, double, std::less<>> stats;
};

struct TargetProfile {
    std::string scope_half;
    std::string target_id;
    std::string damage_attribute;
    double resistance{0.20};
    std::optional<double> enemy_defense_base;
};

struct Hit {
    std::string event_id;
    int sequence{};
    std::int64_t relative_time_us{};
    std::string scope_half;
    std::string target_id;
    std::optional<int> character_id;
    double damage{};
    std::string direction;
    std::string classification;
    std::string damage_attribute;
    std::string scaling_property_id;
    std::string critical_policy;
    std::string critical_state;
    std::optional<double> critical_rate;
};

struct Modifier {
    std::string property_id;
    std::string operation;
    std::optional<double> value;
    std::string value_confidence;
    std::string calculation_asset_path;
};

struct Interval {
    std::string interval_id;
    std::int64_t start_us{};
    std::int64_t end_us{};
    std::string scope_half;
    int source_character_id{};
    std::string target_scope;
    std::string target_id;
    std::vector<Modifier> modifiers;
};

struct BuffGroup {
    std::string buff_key;
    std::vector<Interval> intervals;
};

struct Request {
    std::string schema_version;
    std::string model_version;
    std::string dataset_version;
    Axis axis;
    std::vector<Character> characters;
    std::vector<TargetProfile> target_profiles;
    std::vector<Hit> hits;
    std::vector<BuffGroup> buffs;
};

struct HitResult {
    std::string event_id;
    std::string status;
    std::optional<double> quantified_ratio;
    std::optional<double> candidate_damage;
    std::vector<std::string> gap_codes;
};

struct BuffResult {
    std::string buff_key;
    std::string status;
    double basis_damage{};
    double fully_quantified_damage{};
    double partially_quantified_damage{};
    double unavailable_damage{};
    double proven_unchanged_damage{};
    std::optional<double> quantified_increment;
    std::vector<std::string> gap_codes;
    std::vector<HitResult> hits;
};

struct Response {
    std::string schema_version{"counterfactual-response-v1"};
    std::string model_version;
    std::string dataset_version;
    std::vector<BuffResult> results;
};

[[nodiscard]] Request request_from_json(const json::Value& value);
[[nodiscard]] json::Value response_to_json(const Response& response);
void validate_request(const Request& request);

}  // namespace counterfactual
