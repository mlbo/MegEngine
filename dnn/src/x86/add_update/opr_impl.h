/**
 * \file dnn/src/x86/add_update/opr_impl.h
 * MegEngine is Licensed under the Apache License, Version 2.0 (the "License")
 *
 * Copyright (c) 2014-2021 Megvii Inc. All rights reserved.
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT ARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 */
#pragma once
#include "megdnn/oprs.h"
#include "src/fallback/add_update/opr_impl.h"

namespace megdnn {
namespace x86 {

class AddUpdateImpl : public ::megdnn::fallback::AddUpdateImpl {
public:
    using ::megdnn::fallback::AddUpdateImpl::AddUpdateImpl;
    void exec(_megdnn_tensor_inout dest, _megdnn_tensor_in delta) override;

    bool is_thread_safe() const override { return true; }
};

}  // namespace x86
}  // namespace megdnn
// vim: syntax=cpp.doxygen
